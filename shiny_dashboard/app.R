library(shiny)
library(shinydashboard)
library(shinyjs)
library(bcrypt)
library(digest)
library(mongolite)
library(DT)
library(dplyr)
library(lubridate)
library(rmarkdown)
library(httr)
library(jsonlite)
library(plotly)
library(forcats)
library(leaflet)

`%||%` <- function(a, b) if (!is.null(a) && nchar(a) > 0) a else b

# ── Auth / Security helper functions ──────────────────────────────────────────

UF_BANNER_HTML <- '<hr><div style="text-align:center;background:#003087;color:white;padding:10px;margin-top:20px;font-family:Arial,sans-serif;"><strong>University of Florida \u2014 FPREN</strong><br>Florida Public Radio Emergency Network<br><small>Information Technology | University of Florida | Gainesville, FL 32611</small></div>'

AUP_TEXT <- "The user understands and acknowledges that the computer and the network are the property of the University of Florida. The user agrees to comply with the University of Florida Acceptable User Policy and Guidelines. Unauthorized use of this system is prohibited and subject to criminal and civil penalties. The university monitors computer and network activities without user authorization, and the university may provide information about computer or network usage to the university officials, including law enforcement when warranted. Therefore, the user should have limited expectations of privacy."

gen_token <- function(n = 32) {
  paste0(sample(c(letters, LETTERS, 0:9), n, replace = TRUE), collapse = "")
}

gen_code6 <- function() {
  sprintf("%06d", sample(0:999999, 1))
}

send_fpren_email <- function(to, subject, body_html) {
  sc <- tryCatch(fromJSON("/home/ufuser/Fpren-main/weather_rss/config/smtp_config.json"),
                 error = function(e) list())
  smtp_host <- sc$smtp_host %||% "smtp.ufl.edu"
  smtp_port <- as.integer(if (!is.null(sc$smtp_port) && sc$smtp_port != "") sc$smtp_port else 25)
  mail_from <- sc$mail_from %||% "lawrence.bornace@ufl.edu"
  full_html  <- paste0('<html><body style="font-family:Arial,sans-serif;">', body_html, UF_BANNER_HTML, '</body></html>')
  # Delegate to Python helper script — avoids emayili/shinyjs namespace conflicts
  tryCatch({
    tmp_html <- tempfile(fileext = ".html")
    tmp_cfg  <- tempfile(fileext = ".json")
    writeLines(full_html, tmp_html)
    write_json(
      list(to = to, subject = subject, mail_from = mail_from,
           smtp_host = smtp_host, smtp_port = smtp_port,
           use_tls  = isTRUE(sc$use_tls),
           use_auth = isTRUE(sc$use_auth),
           smtp_user = sc$smtp_user %||% "",
           smtp_pass = sc$smtp_pass %||% ""),
      tmp_cfg, auto_unbox = TRUE)
    py_helper <- "/home/ufuser/Fpren-main/shiny_dashboard/send_email.py"
    result <- system2("python3", args = c(py_helper, tmp_cfg, tmp_html),
                      stdout = TRUE, stderr = TRUE)
    try(file.remove(tmp_html), silent = TRUE)
    try(file.remove(tmp_cfg),  silent = TRUE)
    if (any(grepl("^OK", result))) { TRUE } else {
      message("Email failed: ", paste(result, collapse = " ")); FALSE
    }
  }, error = function(e) { message("Email error: ", e$message); FALSE })
}

send_twilio_sms <- function(to_phone, body_text) {
  cfg <- tryCatch(fromJSON("/home/ufuser/Fpren-main/stream_notify_config.json"),
                  error = function(e) list())
  sid   <- cfg$twilio_sid   %||% ""
  token <- cfg$twilio_token %||% ""
  from  <- cfg$twilio_from  %||% ""
  if (nchar(sid) == 0 || nchar(token) == 0 || nchar(from) == 0) {
    message("Twilio not configured"); return(FALSE)
  }
  url <- paste0("https://api.twilio.com/2010-04-01/Accounts/", sid, "/Messages.json")
  tryCatch({
    r <- httr::POST(url,
      httr::authenticate(sid, token),
      body = list(From = from, To = to_phone, Body = body_text),
      encode = "form")
    httr::status_code(r) %in% c(200, 201)
  }, error = function(e) { message("SMS failed: ", e$message); FALSE })
}

log_audit <- function(action, target_user, performed_by, details = "") {
  col <- tryCatch(
    mongo(collection = "user_audit_log", db = "weather_rss",
          url = Sys.getenv("MONGO_URI", "mongodb://localhost:27017/")),
    error = function(e) NULL)
  if (is.null(col)) return(invisible(NULL))
  tryCatch({
    col$insert(data.frame(
      action       = action,
      target_user  = target_user,
      performed_by = performed_by,
      timestamp    = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
      details      = details,
      stringsAsFactors = FALSE
    ))
    col$disconnect()
  }, error = function(e) {
    tryCatch(col$disconnect(), error = function(e2) NULL)
  })
}

send_notification_emails <- function(subject, body_html) {
  col <- tryCatch(
    mongo(collection = "notification_config", db = "weather_rss",
          url = Sys.getenv("MONGO_URI", "mongodb://localhost:27017/")),
    error = function(e) NULL)
  if (is.null(col)) return(invisible(NULL))
  cfg <- tryCatch({
    r <- col$find('{"_id":"singleton"}')
    col$disconnect()
    r
  }, error = function(e) {
    tryCatch(col$disconnect(), error = function(e2) NULL)
    data.frame()
  })
  if (nrow(cfg) == 0 || is.null(cfg$notify_emails)) return(invisible(NULL))
  emails <- trimws(unlist(strsplit(cfg$notify_emails, ",")))
  emails <- emails[nchar(emails) > 0]
  for (em in emails) {
    tryCatch(send_fpren_email(em, subject, body_html), error = function(e) NULL)
  }
}

STREAM_NOTIFY_CONFIG <- "/home/ufuser/Fpren-main/stream_notify_config.json"
STREAM_STATE_FILE    <- "/home/ufuser/Fpren-main/logs/stream_state.txt"
STREAM_NOTIFY_LOG    <- "/home/ufuser/Fpren-main/logs/stream_notifications.log"

read_notify_config <- function() {
  if (file.exists(STREAM_NOTIFY_CONFIG)) {
    tryCatch(fromJSON(STREAM_NOTIFY_CONFIG), error = function(e) list())
  } else {
    list(notify_methods = "email", email = "", phone = "",
         twilio_sid = "", twilio_token = "", twilio_from = "",
         notify_on_offline = TRUE, notify_on_reboot = TRUE)
  }
}

save_notify_config <- function(cfg) {
  tryCatch(
    write(toJSON(cfg, auto_unbox = TRUE, pretty = TRUE), STREAM_NOTIFY_CONFIG),
    error = function(e) NULL
  )
}

check_stream_port <- function(host = "127.0.0.1", port = 8000, timeout = 3) {
  tryCatch({
    con <- socketConnection(host = host, port = port, open = "r+",
                            blocking = TRUE, timeout = timeout)
    close(con)
    TRUE
  }, error = function(e) FALSE)
}

read_stream_state <- function() {
  if (file.exists(STREAM_STATE_FILE))
    trimws(readLines(STREAM_STATE_FILE, warn = FALSE)[1])
  else
    "unknown"
}

read_notify_log <- function(n = 20) {
  if (!file.exists(STREAM_NOTIFY_LOG)) return(data.frame(Message = "No notifications yet"))
  lines <- tryCatch(tail(readLines(STREAM_NOTIFY_LOG, warn = FALSE), n), error = function(e) character(0))
  if (length(lines) == 0) return(data.frame(Message = "No notifications yet"))
  data.frame(Log = rev(lines), stringsAsFactors = FALSE)
}

# ── MongoDB connections ───────────────────────────────────────────────────────
MONGO_URI <- Sys.getenv("MONGO_URI", "mongodb://localhost:27017/")

# ── Florida ZIP → County lookup ───────────────────────────────────────────────
FLORIDA_COUNTIES_LIST <- c(
  "Alachua","Baker","Bay","Bradford","Brevard","Broward","Calhoun","Charlotte",
  "Citrus","Clay","Collier","Columbia","Miami-Dade","DeSoto","Dixie","Duval",
  "Escambia","Flagler","Franklin","Gadsden","Gilchrist","Glades","Gulf","Hamilton",
  "Hardee","Hendry","Hernando","Highlands","Hillsborough","Holmes","Indian River",
  "Jackson","Jefferson","Lafayette","Lake","Lee","Leon","Levy","Liberty","Madison",
  "Manatee","Marion","Martin","Monroe","Nassau","Okaloosa","Okeechobee","Orange",
  "Osceola","Palm Beach","Pasco","Pinellas","Polk","Putnam","Saint Johns",
  "Saint Lucie","Santa Rosa","Sarasota","Seminole","Sumter","Suwannee","Taylor",
  "Union","Volusia","Wakulla","Walton","Washington"
)

# County → Icecast zone mapping (mirrors zone_definitions)
COUNTY_TO_ZONE <- c(
  "Alachua"="gainesville","Bradford"="gainesville",
  "Baker"="jacksonville","Clay"="jacksonville","Duval"="jacksonville",
  "Nassau"="jacksonville","Saint Johns"="jacksonville",
  "Seminole"="orlando","Orange"="orlando","Osceola"="orlando",
  "Hillsborough"="tampa","Pinellas"="tampa","Pasco"="tampa",
  "Broward"="miami","Miami-Dade"="miami",
  "Escambia"="north_florida","Santa Rosa"="north_florida","Okaloosa"="north_florida",
  "Walton"="north_florida","Holmes"="north_florida","Washington"="north_florida",
  "Bay"="north_florida","Jackson"="north_florida","Calhoun"="north_florida",
  "Gulf"="north_florida","Franklin"="north_florida","Gadsden"="north_florida",
  "Liberty"="north_florida","Leon"="north_florida","Wakulla"="north_florida",
  "Jefferson"="north_florida","Madison"="north_florida","Taylor"="north_florida",
  "Hamilton"="north_florida","Suwannee"="north_florida","Lafayette"="north_florida",
  "Columbia"="north_florida","Union"="north_florida","Gilchrist"="north_florida",
  "Dixie"="north_florida","Levy"="north_florida",
  "Putnam"="north_florida","Flagler"="north_florida",
  "Volusia"="central_florida","Lake"="central_florida","Marion"="central_florida",
  "Citrus"="central_florida","Hernando"="central_florida","Sumter"="central_florida",
  "Brevard"="central_florida","Indian River"="central_florida",
  "Polk"="central_florida","Highlands"="central_florida","Hardee"="central_florida",
  "Manatee"="central_florida","Sarasota"="central_florida",
  "Charlotte"="south_florida","DeSoto"="south_florida","Lee"="south_florida",
  "Collier"="south_florida","Hendry"="south_florida","Glades"="south_florida",
  "Okeechobee"="south_florida","Martin"="south_florida","Palm Beach"="south_florida",
  "Saint Lucie"="south_florida","Monroe"="south_florida"
)

# ZIP ranges: each row is c(from, to, county)
# First matching range wins (specific ranges listed before broad ones)
.fl_zip_data <- rbind(
  # Alachua
  c(32601,32699,"Alachua"),
  # Baker
  c(32040,32040,"Baker"),c(32063,32063,"Baker"),c(32067,32067,"Baker"),
  c(32072,32072,"Baker"),c(32087,32087,"Baker"),
  # Bay
  c(32401,32417,"Bay"),c(32444,32444,"Bay"),
  # Bradford
  c(32042,32042,"Bradford"),c(32044,32044,"Bradford"),
  c(32058,32058,"Bradford"),c(32083,32083,"Bradford"),c(32091,32091,"Bradford"),
  # Brevard
  c(32754,32754,"Brevard"),c(32775,32775,"Brevard"),
  c(32899,32899,"Brevard"),c(32901,32999,"Brevard"),
  # Broward
  c(33004,33004,"Broward"),c(33008,33009,"Broward"),
  c(33019,33029,"Broward"),c(33060,33093,"Broward"),
  c(33301,33340,"Broward"),
  # Calhoun
  c(32421,32421,"Calhoun"),c(32424,32424,"Calhoun"),
  c(32430,32430,"Calhoun"),c(32449,32449,"Calhoun"),
  # Charlotte
  c(33946,33955,"Charlotte"),c(33980,33983,"Charlotte"),
  # Citrus
  c(34428,34461,"Citrus"),
  # Clay
  c(32003,32003,"Clay"),c(32043,32043,"Clay"),
  c(32065,32065,"Clay"),c(32068,32068,"Clay"),c(32073,32073,"Clay"),
  # Collier
  c(34101,34120,"Collier"),c(34133,34146,"Collier"),
  # Columbia
  c(32024,32025,"Columbia"),c(32038,32038,"Columbia"),
  c(32055,32055,"Columbia"),c(32061,32061,"Columbia"),
  # Miami-Dade
  c(33010,33018,"Miami-Dade"),c(33030,33039,"Miami-Dade"),
  c(33101,33200,"Miami-Dade"),c(33255,33261,"Miami-Dade"),
  c(33265,33266,"Miami-Dade"),c(33269,33270,"Miami-Dade"),
  c(33280,33280,"Miami-Dade"),c(33283,33283,"Miami-Dade"),
  c(33296,33296,"Miami-Dade"),
  # DeSoto
  c(34266,34269,"DeSoto"),
  # Dixie
  c(32628,32628,"Dixie"),c(32648,32648,"Dixie"),c(32680,32680,"Dixie"),
  # Duval (32004, 32099, 32201-32260)
  c(32004,32004,"Duval"),c(32099,32099,"Duval"),c(32201,32260,"Duval"),
  # Escambia
  c(32501,32529,"Escambia"),c(32590,32599,"Escambia"),
  # Flagler
  c(32110,32110,"Flagler"),c(32136,32137,"Flagler"),c(32164,32164,"Flagler"),
  # Franklin
  c(32320,32323,"Franklin"),c(32328,32329,"Franklin"),c(32346,32346,"Franklin"),
  # Gadsden
  c(32324,32324,"Gadsden"),c(32330,32330,"Gadsden"),
  c(32332,32333,"Gadsden"),c(32343,32343,"Gadsden"),c(32351,32353,"Gadsden"),
  # Gilchrist
  c(32619,32619,"Gilchrist"),c(32693,32693,"Gilchrist"),
  # Glades
  c(33430,33430,"Glades"),c(33471,33471,"Glades"),
  # Gulf
  c(32456,32457,"Gulf"),c(32465,32465,"Gulf"),
  # Hamilton
  c(32052,32053,"Hamilton"),c(32096,32096,"Hamilton"),
  # Hardee
  c(33834,33836,"Hardee"),c(33873,33873,"Hardee"),
  # Hendry
  c(33440,33440,"Hendry"),c(33935,33935,"Hendry"),
  # Hernando
  c(34601,34614,"Hernando"),
  # Highlands
  c(33825,33825,"Highlands"),c(33852,33876,"Highlands"),
  # Hillsborough
  c(33502,33511,"Hillsborough"),c(33527,33527,"Hillsborough"),
  c(33534,33534,"Hillsborough"),c(33547,33550,"Hillsborough"),
  c(33556,33556,"Hillsborough"),c(33563,33573,"Hillsborough"),
  c(33578,33579,"Hillsborough"),c(33583,33587,"Hillsborough"),
  c(33592,33598,"Hillsborough"),c(33601,33650,"Hillsborough"),
  # Holmes
  c(32425,32425,"Holmes"),
  # Indian River
  c(32948,32948,"Indian River"),c(32960,32968,"Indian River"),
  # Jackson
  c(32420,32420,"Jackson"),c(32423,32423,"Jackson"),
  c(32426,32426,"Jackson"),c(32431,32432,"Jackson"),
  c(32440,32443,"Jackson"),c(32445,32448,"Jackson"),c(32460,32460,"Jackson"),
  # Jefferson
  c(32336,32336,"Jefferson"),c(32344,32344,"Jefferson"),
  c(32357,32357,"Jefferson"),c(32364,32364,"Jefferson"),
  # Lafayette
  c(32013,32013,"Lafayette"),c(32066,32066,"Lafayette"),
  # Lake
  c(32702,32702,"Lake"),c(32726,32727,"Lake"),c(32735,32737,"Lake"),
  c(32756,32756,"Lake"),c(32767,32767,"Lake"),c(32776,32776,"Lake"),
  c(32778,32778,"Lake"),c(32783,32784,"Lake"),
  c(34711,34737,"Lake"),c(34748,34749,"Lake"),c(34753,34753,"Lake"),
  c(34755,34756,"Lake"),c(34762,34762,"Lake"),c(34788,34788,"Lake"),c(34797,34797,"Lake"),
  # Lee
  c(33901,33945,"Lee"),c(33965,33976,"Lee"),c(33990,33999,"Lee"),
  # Leon
  c(32301,32399,"Leon"),
  # Levy
  c(32621,32621,"Levy"),c(32625,32626,"Levy"),c(32668,32668,"Levy"),
  # Liberty
  c(32314,32314,"Liberty"),c(32321,32321,"Liberty"),
  c(32334,32335,"Liberty"),c(32360,32360,"Liberty"),
  # Madison
  c(32059,32059,"Madison"),c(32340,32341,"Madison"),c(32350,32350,"Madison"),
  # Manatee
  c(34201,34221,"Manatee"),c(34243,34243,"Manatee"),c(34251,34251,"Manatee"),
  # Marion
  c(32113,32113,"Marion"),c(32134,32134,"Marion"),c(32179,32179,"Marion"),
  c(32195,32195,"Marion"),c(32617,32617,"Marion"),c(32686,32686,"Marion"),
  c(34420,34432,"Marion"),c(34470,34491,"Marion"),
  # Martin
  c(34953,34957,"Martin"),c(34990,34997,"Martin"),
  # Monroe
  c(33001,33001,"Monroe"),c(33037,33037,"Monroe"),
  c(33040,33045,"Monroe"),c(33050,33057,"Monroe"),
  # Nassau
  c(32009,32009,"Nassau"),c(32011,32011,"Nassau"),
  c(32034,32034,"Nassau"),c(32046,32046,"Nassau"),c(32097,32097,"Nassau"),
  # Okaloosa
  c(32531,32532,"Okaloosa"),c(32536,32542,"Okaloosa"),
  c(32544,32544,"Okaloosa"),c(32547,32549,"Okaloosa"),c(32564,32567,"Okaloosa"),
  # Okeechobee
  c(34972,34974,"Okeechobee"),
  # Orange
  c(32703,32703,"Orange"),c(32710,32710,"Orange"),c(32712,32712,"Orange"),
  c(32719,32719,"Orange"),c(32732,32732,"Orange"),c(32739,32742,"Orange"),
  c(32757,32760,"Orange"),c(32762,32762,"Orange"),c(32768,32768,"Orange"),
  c(32777,32777,"Orange"),c(32789,32812,"Orange"),c(32814,32839,"Orange"),
  c(32853,32862,"Orange"),c(32867,32869,"Orange"),c(32872,32872,"Orange"),
  c(32877,32878,"Orange"),c(32883,32886,"Orange"),
  c(34734,34734,"Orange"),c(34760,34761,"Orange"),
  c(34777,34778,"Orange"),c(34787,34787,"Orange"),
  # Osceola
  c(34739,34739,"Osceola"),c(34741,34747,"Osceola"),
  c(34769,34769,"Osceola"),c(34771,34773,"Osceola"),
  # Palm Beach
  c(33401,33499,"Palm Beach"),
  # Pasco
  c(33523,33526,"Pasco"),c(33535,33536,"Pasco"),
  c(33539,33545,"Pasco"),c(33558,33559,"Pasco"),
  c(33574,33574,"Pasco"),c(33576,33576,"Pasco"),
  c(34637,34639,"Pasco"),c(34652,34660,"Pasco"),
  c(34667,34669,"Pasco"),c(34679,34679,"Pasco"),
  # Pinellas
  c(33701,33716,"Pinellas"),c(33729,33731,"Pinellas"),
  c(33733,33734,"Pinellas"),c(33736,33736,"Pinellas"),
  c(33738,33738,"Pinellas"),c(33740,33742,"Pinellas"),
  c(33744,33744,"Pinellas"),c(33747,33747,"Pinellas"),
  c(33755,33784,"Pinellas"),c(33785,33786,"Pinellas"),
  c(34677,34677,"Pinellas"),c(34680,34698,"Pinellas"),
  # Polk
  c(33801,33898,"Polk"),
  # Putnam
  c(32112,32112,"Putnam"),c(32131,32131,"Putnam"),c(32139,32139,"Putnam"),
  c(32148,32149,"Putnam"),c(32177,32177,"Putnam"),c(32181,32181,"Putnam"),
  # Saint Johns
  c(32033,32033,"Saint Johns"),c(32080,32086,"Saint Johns"),
  c(32092,32092,"Saint Johns"),c(32095,32095,"Saint Johns"),c(32259,32259,"Saint Johns"),
  # Saint Lucie
  c(34945,34946,"Saint Lucie"),c(34950,34952,"Saint Lucie"),
  c(34958,34958,"Saint Lucie"),c(34981,34988,"Saint Lucie"),
  # Santa Rosa
  c(32530,32530,"Santa Rosa"),c(32533,32535,"Santa Rosa"),
  c(32560,32563,"Santa Rosa"),c(32568,32571,"Santa Rosa"),
  c(32578,32580,"Santa Rosa"),c(32583,32583,"Santa Rosa"),
  # Sarasota
  c(34228,34242,"Sarasota"),c(34272,34295,"Sarasota"),
  # Seminole
  c(32700,32701,"Seminole"),c(32704,32704,"Seminole"),
  c(32707,32709,"Seminole"),c(32714,32714,"Seminole"),
  c(32716,32716,"Seminole"),c(32718,32718,"Seminole"),
  c(32730,32731,"Seminole"),c(32733,32733,"Seminole"),
  c(32745,32746,"Seminole"),c(32747,32747,"Seminole"),
  c(32750,32752,"Seminole"),c(32761,32761,"Seminole"),
  c(32765,32766,"Seminole"),c(32769,32773,"Seminole"),c(32779,32782,"Seminole"),
  # Sumter
  c(33513,33513,"Sumter"),c(33538,33538,"Sumter"),
  c(33585,33585,"Sumter"),c(34484,34484,"Sumter"),c(34785,34785,"Sumter"),
  # Suwannee
  c(32008,32008,"Suwannee"),c(32060,32060,"Suwannee"),
  c(32062,32062,"Suwannee"),c(32064,32064,"Suwannee"),
  # Taylor
  c(32347,32348,"Taylor"),c(32356,32356,"Taylor"),c(32359,32359,"Taylor"),
  # Union
  c(32054,32054,"Union"),
  # Volusia
  c(32101,32109,"Volusia"),c(32114,32135,"Volusia"),
  c(32141,32141,"Volusia"),c(32160,32163,"Volusia"),
  c(32168,32169,"Volusia"),c(32174,32176,"Volusia"),
  c(32180,32180,"Volusia"),c(32190,32190,"Volusia"),
  c(32198,32198,"Volusia"),c(32706,32706,"Volusia"),
  c(32713,32713,"Volusia"),c(32720,32725,"Volusia"),
  c(32728,32728,"Volusia"),c(32738,32738,"Volusia"),
  c(32744,32744,"Volusia"),c(32753,32753,"Volusia"),
  c(32763,32764,"Volusia"),c(32774,32774,"Volusia"),
  # Wakulla
  c(32327,32327,"Wakulla"),c(32355,32355,"Wakulla"),
  c(32358,32358,"Wakulla"),c(32361,32361,"Wakulla"),c(32395,32399,"Wakulla"),
  # Walton
  c(32435,32436,"Walton"),c(32439,32439,"Walton"),
  c(32459,32461,"Walton"),c(32462,32464,"Walton"),c(32466,32466,"Walton"),
  # Washington
  c(32427,32428,"Washington"),c(32437,32438,"Washington"),c(32442,32442,"Washington")
)
FL_ZIP_RANGES <- data.frame(
  from   = as.integer(.fl_zip_data[,1]),
  to     = as.integer(.fl_zip_data[,2]),
  county = .fl_zip_data[,3],
  stringsAsFactors = FALSE
)
rm(.fl_zip_data)

zip_to_florida_county <- function(zip_str) {
  z <- trimws(as.character(zip_str))
  if (!grepl("^\\d{5}$", z)) return(NA_character_)
  n <- as.integer(z)
  if (n < 32004L || n > 34997L) return(NA_character_)
  idx <- which(FL_ZIP_RANGES$from <= n & FL_ZIP_RANGES$to >= n)
  if (length(idx) == 0L) return(NA_character_)
  FL_ZIP_RANGES$county[idx[1L]]
}

get_col <- function(collection) {
  tryCatch(
    mongo(collection = collection, db = "weather_rss", url = MONGO_URI),
    error = function(e) NULL
  )
}

# ── UI ────────────────────────────────────────────────────────────────────────

# Login screen HTML (shown before dashboard)
login_screen_ui <- div(
  id = "login_screen",
  style = "position:fixed;top:0;left:0;width:100%;height:100%;background:#f4f4f4;z-index:9999;display:flex;align-items:center;justify-content:center;overflow-y:auto;",
  div(style = "background:white;padding:40px;border-radius:8px;box-shadow:0 4px 20px rgba(0,0,0,0.15);max-width:520px;width:100%;margin:auto;",
    div(style = "text-align:center;margin-bottom:20px;",
      tags$div(style = "background:#003087;color:white;padding:16px;border-radius:6px;margin-bottom:12px;",
        tags$h2(style = "margin:0;font-size:24px;font-weight:bold;", "FPREN"),
        tags$p(style = "margin:4px 0 0;font-size:13px;", "Florida Public Radio Emergency Network"),
        tags$p(style = "margin:2px 0 0;font-size:11px;opacity:0.8;", "University of Florida")
      )
    ),
    tags$div(
      textInput("login_username", "Username", placeholder = "Enter username"),
      passwordInput("login_password", "Password", placeholder = "Enter password"),
      uiOutput("login_attempts_msg"),
      br(),
      actionButton("btn_login", "Login", class = "btn-primary btn-block",
                   width = "100%", style = "font-size:16px;padding:10px;"),
      br(),
      actionButton("btn_forgot", "Forgot username or password?",
                   class = "btn-link", style = "width:100%;text-align:center;"),
      hr(),
      tags$div(
        style = "background:#fff3cd;border:1px solid #ffc107;border-radius:4px;padding:12px;font-size:12px;color:#333;",
        tags$strong("NOTICE \u2014 Acceptable Use Policy"),
        tags$p(style = "margin-top:6px;margin-bottom:0;", AUP_TEXT)
      )
    )
  )
)

ui <- tagList(
  useShinyjs(),
  login_screen_ui,
  div(id = "main_dashboard", style = "display:none;",
  dashboardPage(
  skin  = "blue",
  title = "FPREN",

  dashboardHeader(
    title = tags$span(
      style = "font-size: 13px; font-weight: bold; line-height: 1.35; white-space: normal;",
      "Florida Public Radio Emergency Network"
    ),
    titleWidth = 280
  ),

  dashboardSidebar(width = 280,
    sidebarMenu(
      menuItem("Overview",              tabName = "overview",       icon = icon("tachometer-alt")),
      menuItem("Weather Conditions",    tabName = "wx_cities",      icon = icon("cloud-sun")),
      menuItem("FL Alerts",             tabName = "alerts",         icon = icon("exclamation-triangle")),
      menuItem("Traffic Alerts",        tabName = "traffic_alerts", icon = icon("car-crash")),
      menuItem("Traffic Analysis",      tabName = "traffic_analysis", icon = icon("chart-bar")),
      menuItem("County Alerts",         tabName = "county_alerts",  icon = icon("map-marker-alt")),
      menuItem("Airport Delays & Weather", tabName = "airports",   icon = icon("plane")),
      menuItem("Upload Content",        tabName = "upload",         icon = icon("upload")),
      menuItem("Reports",               tabName = "reports",        icon = icon("file-pdf")),
      menuItem("Station Health",        tabName = "health",         icon = icon("heartbeat")),
      menuItem("Icecast Streams",       tabName = "icecast",        icon = icon("broadcast-tower")),
      menuItem("Feed Status",           tabName = "feeds",          icon = icon("rss")),
      menuItem("Zones",                 tabName = "zones",          icon = icon("map")),
      menuItem("Config",                tabName = "config",         icon = icon("cog"))
    )
  ),

  dashboardBody(
    tags$head(
      tags$script(HTML("
        (function() {
          var idleMinutes = 0;
          var warnShown = false;
          function resetIdle() {
            idleMinutes = 0;
            warnShown = false;
            if (window.Shiny) Shiny.setInputValue('user_activity_ping', Math.random());
          }
          document.addEventListener('mousemove', resetIdle, true);
          document.addEventListener('keydown',   resetIdle, true);
          document.addEventListener('click',     resetIdle, true);
          document.addEventListener('scroll',    resetIdle, true);
          setInterval(function() {
            var ls = document.getElementById('login_screen');
            if (ls && ls.style.display !== 'none') return;
            idleMinutes++;
            if (idleMinutes >= 2 && !warnShown) {
              warnShown = true;
              if (window.Shiny) Shiny.setInputValue('idle_warn', Math.random());
            }
            if (idleMinutes >= 3) {
              if (window.Shiny) Shiny.setInputValue('idle_logout', Math.random());
            }
          }, 60000);
        })();
      ")),
      tags$style(HTML("
      .content-wrapper { background-color: #f4f6f9; }
      .small-box .icon { font-size: 60px; }
      .alert-extreme { background-color: #f56954 !important; color: white !important; }
      .alert-severe  { background-color: #f39c12 !important; color: white !important; }
      .wx-card { border-radius: 8px; padding: 14px; margin-bottom: 14px;
                 color: white; min-height: 180px; }
      .wx-card.vfr  { background-color: #1a6bb5; }
      .wx-card.mvfr { background-color: #b5860a; }
      .wx-card.ifr  { background-color: #c0460a; }
      .wx-card.lifr { background-color: #8b0000; }
      .wx-card.unknown { background-color: #5a5a5a; }
      .wx-city { font-size: 16px; font-weight: bold; margin-bottom: 4px; }
      .wx-temp  { font-size: 36px; font-weight: bold; line-height: 1.1; }
      .wx-feels { font-size: 13px; opacity: 0.85; margin-bottom: 6px; }
      .wx-desc  { font-size: 13px; font-style: italic; margin-bottom: 6px; }
      .wx-detail { font-size: 12px; opacity: 0.9; }
      .wx-cat   { font-size: 11px; font-weight: bold; letter-spacing: 1px;
                  background: rgba(255,255,255,0.25); border-radius: 4px;
                  padding: 1px 6px; display: inline-block; margin-bottom: 4px; }
      .wx-time  { font-size: 11px; opacity: 0.7; margin-top: 6px; }
      .fl-radar-wrap { background:#0d1117; border-radius:6px; padding:8px; text-align:center; }
      .fl-radar-wrap img { max-width:100%; border-radius:4px; display:block; margin:0 auto; }
      .fl-radar-ts { font-size:10px; color:#aaa; margin-top:4px; }
      .zip-radar-wrap { background:#0d1117; border-radius:6px; padding:8px;
                        text-align:center; margin-top:10px; }
      .zip-radar-wrap img { max-width:100%; border-radius:4px; display:block; margin:0 auto; }
      .zip-panel { background:#fff; border-radius:8px; padding:16px;
                   margin-bottom:18px; border-left:4px solid #3c8dbc; }
      .zip-panel h4  { margin-top:0; color:#3c8dbc; }
      .zip-panel .zip-loc { font-size:15px; font-weight:bold; margin-bottom:6px; }
      .zip-panel .zip-cur { font-size:13px; color:#555; margin-bottom:10px; }
      .zip-days-scroll { display:flex; overflow-x:auto; gap:10px;
                         padding-bottom:8px; }
      .zip-day-card { flex:0 0 110px; background:#f4f6f9; border-radius:6px;
                      padding:10px 8px; text-align:center; border:1px solid #ddd; }
      .zip-day-card .day-name { font-weight:bold; font-size:13px; color:#333;
                                 margin-bottom:4px; }
      .zip-day-card .day-hi   { font-size:18px; font-weight:bold; color:#c0392b; }
      .zip-day-card .day-lo   { font-size:13px; color:#3498db; }
      .zip-day-card .day-precip { font-size:11px; color:#27ae60; margin-top:3px; }
      .zip-day-card .day-wind   { font-size:11px; color:#7f8c8d; }
      .zip-day-card .day-desc   { font-size:11px; color:#555; margin-top:4px;
                                   line-height:1.3; }
    "))),

    # ── Auto-refresh ZIP-area radar img every 5 minutes (cache-bust) ─────────
    tags$script(HTML("
      setInterval(function() {
        var img2 = document.getElementById('zip-city-radar');
        if (img2) {
          var base2 = img2.getAttribute('data-src');
          if (base2) img2.src = base2 + '&t=' + Date.now();
        }
      }, 300000);
    ")),

    tabItems(

      # ── Overview ────────────────────────────────────────────────────────────
      tabItem(tabName = "overview",
        fluidRow(
          valueBoxOutput("box_fl_alerts",      width = 3),
          valueBoxOutput("box_county_alerts",   width = 3),
          valueBoxOutput("box_airport_delays",  width = 3),
          valueBoxOutput("box_wavs_generated",  width = 3)
        ),
        fluidRow(
          box(title = "Active Florida Alerts", width = 8, status = "danger",
              solidHeader = TRUE, DTOutput("tbl_overview_alerts")),
          box(title = "Station Status", width = 4, status = "info",
              solidHeader = TRUE,
              h4("TTS Engine"),
              verbatimTextOutput("txt_tts_engine"),
              hr(),
              h4("Last Heartbeat"),
              verbatimTextOutput("txt_heartbeat"),
              hr(),
              h4("MongoDB"),
              verbatimTextOutput("txt_mongo_status"))
        ),
        fluidRow(
          box(title = "Auto-refresh", width = 12, status = "primary",
              checkboxInput("auto_refresh", "Auto-refresh every 60 seconds", value = TRUE))
        )
      ),

      # ── FL Alerts ───────────────────────────────────────────────────────────
      tabItem(tabName = "alerts",
        fluidRow(
          box(title = "Active Florida NWS Alerts", width = 12, status = "danger",
              solidHeader = TRUE,
              fluidRow(
                column(4, selectInput("alert_severity", "Filter by Severity",
                  choices = c("All", "Extreme", "Severe", "Moderate", "Minor"),
                  selected = "All")),
                column(4, selectInput("alert_source", "Filter by Source",
                  choices = c("All", "IPAWS", "NWS"),
                  selected = "All")),
                column(4, br(), actionButton("btn_refresh_alerts", "Refresh",
                  class = "btn-primary"))
              ),
              DTOutput("tbl_alerts"))
        )
      ),

      # ── Weather Conditions ───────────────────────────────────────────────────
      tabItem(tabName = "wx_cities",
        # ZIP forecast section
        fluidRow(
          box(title = "ZIP Code Forecast", width = 12, status = "info",
              solidHeader = TRUE,
              fluidRow(
                column(3,
                  textInput("wx_zip", label = "Florida ZIP Code",
                            placeholder = "e.g. 32601")
                ),
                column(2, br(),
                  actionButton("btn_wx_forecast", "Get Forecast",
                               class = "btn-info btn-lg", icon = icon("search"))
                ),
                column(2, br(),
                  actionButton("btn_wx_clear", "Clear",
                               class = "btn-default", icon = icon("times"))
                ),
                column(5, br(),
                  tags$small(style = "color:#555;",
                    icon("info-circle"),
                    " Enter a Florida ZIP to see NWS 7-day forecast for that location")
                )
              )
          )
        ),
        uiOutput("wx_zip_error_ui"),
        uiOutput("wx_zip_forecast_ui"),
        # Florida state radar
        fluidRow(
          box(title = tagList(icon("satellite-dish"), " Florida State Radar (NWS NEXRAD)"),
              width = 12, status = "primary", solidHeader = TRUE, collapsible = TRUE,
              leafletOutput("fl_state_radar_map", height = "330px"),
              div(style = "font-size:11px; color:#888; margin-top:4px;",
                icon("clock"), " NWS NEXRAD base reflectivity \u2014 auto-refreshes every 5 min")
          )
        ),
        # City grid header
        fluidRow(
          box(title = "Florida City Weather Conditions", width = 12, status = "primary",
              solidHeader = TRUE,
              fluidRow(
                column(7, h5(icon("info-circle"),
                  " Current METAR conditions \u2014 auto-refreshes every 15 min")),
                column(5, align = "right",
                  actionButton("btn_wx_toggle_cities", "Show All Cities",
                               class = "btn-sm btn-info", icon = icon("map-marker-alt")),
                  tags$span(" "),
                  actionButton("btn_wx_refresh", "Refresh Now",
                               class = "btn-sm btn-default", icon = icon("sync")))
              )
          )
        ),
        uiOutput("wx_cities_grid")
      ),

      # ── Traffic Alerts ───────────────────────────────────────────────────────
      tabItem(tabName = "traffic_alerts",
        fluidRow(
          valueBoxOutput("box_traffic_total",    width = 3),
          valueBoxOutput("box_traffic_major",    width = 3),
          valueBoxOutput("box_traffic_closures", width = 3),
          valueBoxOutput("box_traffic_counties", width = 3)
        ),
        fluidRow(
          box(title = "Filters", width = 12, status = "primary", solidHeader = TRUE,
              fluidRow(
                column(3, selectInput("traffic_county", "County",
                  choices = c("All Counties" = ""), selected = "")),
                column(3, selectInput("traffic_severity", "Severity",
                  choices = c("All" = "", "Major" = "Major", "Minor" = "Minor"),
                  selected = "")),
                column(3, selectInput("traffic_type", "Incident Type",
                  choices = c("All Types" = ""), selected = "")),
                column(3, br(),
                  actionButton("btn_traffic_refresh", "Refresh",
                               class = "btn-primary", icon = icon("sync")))
              )
          )
        ),
        fluidRow(
          box(title = "Active FL511 Traffic Incidents", width = 12, status = "warning",
              solidHeader = TRUE, DTOutput("tbl_traffic"))
        ),
        fluidRow(
          box(title = "Interactive Map", width = 12, status = "info", solidHeader = TRUE,
              p(icon("map"), " Interactive map coming soon — will show incident pins
                colour-coded by severity across Florida highway network."))
        )
      ),

      # ── Traffic Analysis ─────────────────────────────────────────────────────
      tabItem(tabName = "traffic_analysis",
        fluidRow(
          box(title = "Filters", width = 12, status = "primary", solidHeader = TRUE,
              fluidRow(
                column(3, selectInput("ta_road", "Road / Highway",
                  choices = c("All Roads" = ""), selected = "")),
                column(3, selectInput("ta_type", "Incident Type",
                  choices = c("All Types" = ""), selected = "")),
                column(3, selectInput("ta_severity", "Severity",
                  choices = c("All" = "", "Major" = "Major",
                              "Minor" = "Minor", "Intermediate" = "Intermediate"),
                  selected = "")),
                column(3, selectInput("ta_district", "DOT District",
                  choices = c("All Districts" = ""), selected = ""))
              )
          )
        ),
        fluidRow(
          valueBoxOutput("ta_box_total",    width = 3),
          valueBoxOutput("ta_box_major",    width = 3),
          valueBoxOutput("ta_box_closures", width = 3),
          valueBoxOutput("ta_box_counties", width = 3)
        ),
        fluidRow(
          box(title = "Incidents by Road (Top 20)", width = 6, status = "primary",
              solidHeader = TRUE,
              plotly::plotlyOutput("ta_plot_roads", height = "380px")),
          box(title = "Incident Type Breakdown", width = 6, status = "info",
              solidHeader = TRUE,
              plotly::plotlyOutput("ta_plot_types", height = "380px"))
        ),
        fluidRow(
          box(title = "Incidents by County", width = 6, status = "warning",
              solidHeader = TRUE,
              plotly::plotlyOutput("ta_plot_counties", height = "380px")),
          box(title = "Severity Distribution by Road (Top 15)", width = 6,
              status = "danger", solidHeader = TRUE,
              plotly::plotlyOutput("ta_plot_severity", height = "380px"))
        ),
        fluidRow(
          box(title = "County Hotspot Map", width = 12, status = "success",
              solidHeader = TRUE,
              p(tags$small("Circle size and color = incident count. Click a circle for details.")),
              leaflet::leafletOutput("ta_map", height = "500px"))
        ),
        fluidRow(
          box(title = "Export & Email Report", width = 12, status = "warning",
              solidHeader = TRUE,
              fluidRow(
                column(5, textInput("ta_email", "Email Address",
                  placeholder = "recipient@example.com", value = "")),
                column(7, br(),
                  actionButton("btn_ta_pdf",   "Generate PDF",
                               class = "btn-primary", icon = icon("file-pdf")),
                  tags$span(" "),
                  actionButton("btn_ta_email", "Generate & Email PDF",
                               class = "btn-success", icon = icon("envelope")),
                  tags$span(" "),
                  downloadButton("ta_download", "Download CSV",
                                 class = "btn-default")
                )
              ),
              verbatimTextOutput("ta_export_status")
          )
        ),
        fluidRow(
          box(title = "Detailed Data", width = 12, status = "primary",
              solidHeader = TRUE,
              DTOutput("ta_table"))
        )
      ),

      # ── County Alerts ────────────────────────────────────────────────────────
      tabItem(tabName = "county_alerts",
        # Error / info message bar
        uiOutput("ca_error_ui"),
        # Search inputs
        fluidRow(
          box(title = "Search County Alerts", width = 12, status = "primary",
              solidHeader = TRUE,
              fluidRow(
                column(3,
                  textInput("ca_zip", label = "Florida ZIP Code (5 digits)",
                            placeholder = "e.g. 32601")
                ),
                column(3,
                  selectInput("ca_county", label = "Or Select County",
                    choices  = c("-- Select a county --", FLORIDA_COUNTIES_LIST),
                    selected = "Alachua")
                ),
                column(2,
                  br(),
                  actionButton("btn_ca_search", "Search Alerts",
                               class = "btn-primary btn-lg", icon = icon("search"))
                ),
                column(4,
                  br(),
                  uiOutput("ca_zip_hint")
                )
              )
          )
        ),
        # Summary value boxes
        fluidRow(
          valueBoxOutput("ca_box_name",    width = 4),
          valueBoxOutput("ca_box_total",   width = 4),
          valueBoxOutput("ca_box_updated", width = 4)
        ),
        # Alerts DataTable
        fluidRow(
          box(title = "Active Alerts", width = 12, status = "warning",
              solidHeader = TRUE,
              DTOutput("tbl_ca_alerts"))
        ),
        # Full description of selected alert
        fluidRow(
          box(title = "Alert Description (click a row above)", width = 12, status = "info",
              solidHeader = TRUE,
              verbatimTextOutput("ca_alert_description"))
        ),
        # PDF + email export
        fluidRow(
          box(title = "Export Report", width = 12, status = "success",
              solidHeader = TRUE,
              fluidRow(
                column(3,
                  actionButton("btn_ca_pdf", "Generate PDF Report",
                               class = "btn-primary", icon = icon("file-pdf"))
                ),
                column(3,
                  actionButton("btn_ca_email", "Email Report",
                               class = "btn-default", icon = icon("envelope"))
                ),
                column(6,
                  verbatimTextOutput("ca_report_status")
                )
              )
          )
        )
      ),

      # ── Airport Delays ──────────────────────────────────────────────────────
      tabItem(tabName = "airports",
        fluidRow(
          valueBoxOutput("box_airports_delayed", width = 4),
          valueBoxOutput("box_airports_ok",      width = 4),
          valueBoxOutput("box_airports_total",   width = 4)
        ),
        fluidRow(
          box(title = "Florida Airport Status", width = 12, status = "info",
              solidHeader = TRUE, DTOutput("tbl_airports"))
        )
      ),

      # ── Station Health ──────────────────────────────────────────────────────
      tabItem(tabName = "health",
        fluidRow(
          box(title = "Recent WAV/MP3 Files Generated", width = 8, status = "success",
              solidHeader = TRUE, DTOutput("tbl_wavs")),
          box(title = "System Info", width = 4, status = "primary",
              solidHeader = TRUE,
              h4("Shiny Server"),
              p(icon("check-circle", style="color:green"), " Running on port 3838"),
              hr(),
              h4("R Version"),
              verbatimTextOutput("txt_r_version"),
              hr(),
              h4("MongoDB Collections"),
              verbatimTextOutput("txt_collections"),
              hr(),
              h4("Last Dashboard Refresh"),
              verbatimTextOutput("txt_last_refresh"))
        )
      ),

      # ── Icecast Streams ─────────────────────────────────────────────────────
      tabItem(tabName = "icecast",
        fluidRow(
          valueBoxOutput("box_ice_total_listeners", width = 3),
          valueBoxOutput("box_ice_active_mounts",   width = 3),
          valueBoxOutput("box_ice_peak_listeners",  width = 3),
          valueBoxOutput("box_ice_server_uptime",   width = 3)
        ),
        fluidRow(
          box(title = "Zone Stream Status", width = 12, status = "primary",
              solidHeader = TRUE,
              div(style = "float:right; margin-bottom:8px;",
                actionButton("btn_ice_refresh", "Refresh Now",
                             class = "btn-xs btn-default", icon = icon("sync"))),
              div(style = "clear:both;"),
              DTOutput("tbl_icecast_mounts")
          )
        ),
        fluidRow(
          box(title = "Server Info", width = 6, status = "info",
              solidHeader = TRUE,
              tableOutput("tbl_ice_server_info")
          ),
          box(title = "Stream URLs", width = 6, status = "success",
              solidHeader = TRUE,
              p(tags$small("Internal URLs (port 8000). External access restricted to ",
                           tags$code("/fpren"), " mount by UF IT firewall.")),
              uiOutput("ui_ice_stream_urls")
          )
        )
      ),

      # ── Feed Status ─────────────────────────────────────────────────────────
      tabItem(tabName = "feeds",
        fluidRow(
          box(title = "Feed Health Status", width = 12, status = "primary",
              solidHeader = TRUE, DTOutput("tbl_feeds"))
        )
      ),

      # ── Stream Alerts ───────────────────────────────────────────────────────
      tabItem(tabName = "stream_alerts",
        fluidRow(
          valueBoxOutput("box_stream_status", width = 4),
          valueBoxOutput("box_stream_last_check", width = 4),
          valueBoxOutput("box_stream_state_file", width = 4)
        ),
        fluidRow(
          box(title = "Notification Settings", width = 6, status = "warning",
              solidHeader = TRUE,
              checkboxGroupInput("stream_notify_methods", "Alert me via:",
                choices  = c("Email" = "email", "SMS Text" = "sms", "Phone Call" = "phone"),
                selected = "email",
                inline   = TRUE),
              conditionalPanel("input.stream_notify_methods.indexOf('email') >= 0",
                textInput("stream_email", "Email Address", value = "")
              ),
              conditionalPanel(
                "input.stream_notify_methods.indexOf('sms') >= 0 || input.stream_notify_methods.indexOf('phone') >= 0",
                textInput("stream_phone", "Phone Number (E.164, e.g. +13525551234)", value = ""),
                textInput("stream_twilio_sid",   "Twilio Account SID", value = ""),
                passwordInput("stream_twilio_token", "Twilio Auth Token", value = ""),
                textInput("stream_twilio_from",  "Twilio From Number (E.164)", value = "")
              ),
              hr(),
              checkboxInput("stream_notify_offline", "Notify when stream goes offline", value = TRUE),
              checkboxInput("stream_notify_reboot",  "Notify on server reboot",         value = TRUE),
              br(),
              fluidRow(
                column(6, actionButton("btn_save_stream_cfg", "Save Settings",
                                       class = "btn-primary", icon = icon("save"))),
                column(6, actionButton("btn_test_notify",     "Send Test Notification",
                                       class = "btn-default", icon = icon("paper-plane")))
              ),
              br(),
              verbatimTextOutput("stream_cfg_status")
          ),
          box(title = "Recent Notifications", width = 6, status = "info",
              solidHeader = TRUE,
              actionButton("btn_refresh_notify_log", "Refresh", class = "btn-xs btn-default"),
              br(), br(),
              DTOutput("tbl_notify_log"))
        )
      ),

      # ── Config ──────────────────────────────────────────────────────────────
      tabItem(tabName = "config",
        fluidRow(
          box(title = "SMTP / Email Settings", width = 6, status = "primary",
              solidHeader = TRUE,
              textInput("cfg_smtp_host",  "SMTP Host",     value = ""),
              numericInput("cfg_smtp_port", "SMTP Port",   value = 25, min = 1, max = 65535),
              textInput("cfg_smtp_user",  "SMTP Username", value = ""),
              passwordInput("cfg_smtp_pass", "SMTP Password", value = ""),
              textInput("cfg_mail_from",  "Mail From Address",      value = ""),
              textInput("cfg_mail_to",    "Mail To (default recipient)", value = ""),
              checkboxInput("cfg_use_tls",  "Use STARTTLS",  value = FALSE),
              checkboxInput("cfg_use_auth", "Use SMTP Auth", value = FALSE),
              br(),
              actionButton("btn_save_smtp", "Save SMTP Settings",
                           class = "btn-primary", icon = icon("save")),
              actionButton("btn_test_smtp", "Send Test Email",
                           class = "btn-default", icon = icon("paper-plane")),
              br(), br(),
              verbatimTextOutput("cfg_smtp_status")
          ),
          box(title = "Dashboard Settings", width = 6, status = "info",
              solidHeader = TRUE,
              checkboxInput("cfg_auto_refresh",  "Enable auto-refresh", value = TRUE),
              sliderInput("cfg_refresh_interval", "Refresh interval (seconds)",
                          min = 10, max = 300, value = 60, step = 10),
              hr(),
              h5(icon("info-circle"), " Service Status"),
              tags$table(class = "table table-condensed",
                tags$tbody(
                  tags$tr(tags$td("Icecast (port 8000)"),
                          tags$td(uiOutput("cfg_svc_icecast"))),
                  tags$tr(tags$td("Shiny Server (port 3838)"),
                          tags$td(uiOutput("cfg_svc_shiny"))),
                  tags$tr(tags$td("Flask Dashboard (port 5000)"),
                          tags$td(uiOutput("cfg_svc_flask"))),
                  tags$tr(tags$td("Stream Monitor service"),
                          tags$td(uiOutput("cfg_svc_monitor")))
                )
              ),
              br(),
              actionButton("btn_cfg_refresh_status", "Refresh Status",
                           class = "btn-xs btn-default", icon = icon("sync"))
          )
        ),
        fluidRow(
          box(title = "Notification Email Config", width = 12, status = "info",
              solidHeader = TRUE,
              p(tags$small("Comma-separated list of email addresses to notify on user add/delete events.")),
              textInput("cfg_notify_emails", "Notification Email(s)",
                        placeholder = "admin@ufl.edu, backup@ufl.edu"),
              actionButton("btn_save_notify_emails", "Save Notification Emails",
                           class = "btn-primary", icon = icon("save")),
              verbatimTextOutput("cfg_notify_emails_status")
          )
        ),
        conditionalPanel(
          condition = "output.is_admin",
          fluidRow(
            box(title = "User Management (Admin Only)", width = 12, status = "warning",
                solidHeader = TRUE,
                p(tags$small("Click a row to select a user, then use Delete to remove them.")),
                DT::dataTableOutput("users_table"),
                br(),
                actionButton("btn_delete_user", "Delete Selected User",
                             class = "btn-danger", icon = icon("user-minus")),
                hr(),
                h5("Add New User"),
                p(tags$small("An invite email with a temporary password will be sent to the user's email address.")),
                fluidRow(
                  column(4, textInput("new_user_email", "Email (required)", value = "",
                                      placeholder = "user@ufl.edu")),
                  column(4, textInput("new_user_phone", "Phone (for SMS verification)", value = "",
                                      placeholder = "+13525551234")),
                  column(4, selectInput("new_user_role", "Role",
                    choices = c("admin","operator","viewer"), selected = "viewer"))
                ),
                actionButton("btn_add_user", "Add User & Send Invite",
                             class = "btn-success", icon = icon("user-plus")),
                verbatimTextOutput("user_mgmt_status")
            )
          )
        )
      ),
      tabItem(tabName = "upload",
        fluidRow(
          box(title = "Upload Audio Content", width = 12, status = "primary",
              solidHeader = TRUE,
              selectInput("upload_folder", "Target Folder",
                choices = c("Top of Hour"="top_of_hour","Imaging"="imaging",
                            "Music"="music","Educational"="educational",
                            "Weather Report"="weather_report")),
              fileInput("upload_file", "Choose Audio File(s)",
                multiple = TRUE, accept = c(".mp3",".wav",".ogg",".m4a")),
              actionButton("btn_upload", "Upload Files",
                class = "btn-primary", icon = icon("upload")),
              hr(),
              h5("Files in Selected Folder:"),
              DT::dataTableOutput("upload_file_list"),
              verbatimTextOutput("upload_status")
          )
        )
      ),
      # ── Zones / Playlist Config ──────────────────────────────────────────────
      tabItem(tabName = "zones",
        fluidRow(
          box(title = "Zone", width = 3, status = "primary", solidHeader = TRUE,
              selectInput("zone_pl_sel", NULL,
                choices = c(
                  "All Florida"    = "all_florida",
                  "North Florida"  = "north_florida",
                  "Central Florida"= "central_florida",
                  "South Florida"  = "south_florida",
                  "Tampa"          = "tampa",
                  "Miami"          = "miami",
                  "Orlando"        = "orlando",
                  "Jacksonville"   = "jacksonville",
                  "Gainesville"    = "gainesville"
                ),
                selected = "gainesville"),
              uiOutput("zone_pl_info")
          ),
          box(title = "Normal Mode Playlist", width = 5, status = "success",
              solidHeader = TRUE,
              p(tags$small(icon("info-circle"),
                " These content types rotate in the regular hourly broadcast when no P1 interrupt is active.")),
              checkboxGroupInput("normal_playlist_types", NULL,
                choiceNames = list(
                  "Fire / Red Flag Warnings",
                  "Flood Alerts",
                  "Freeze / Winter Alerts",
                  "Fog Advisories",
                  "Other Alerts",
                  "Weather Reports",
                  "Traffic Alerts",
                  "Airport Weather",
                  "Educational Content",
                  "Imaging / Sweepers",
                  "Top of Hour IDs"
                ),
                choiceValues = list(
                  "fire", "flooding", "freeze", "fog", "other_alerts",
                  "weather_report", "traffic", "airport_weather",
                  "educational", "imaging", "top_of_hour"
                ),
                selected = c("fire","flooding","freeze","fog","other_alerts",
                             "weather_report","traffic","airport_weather",
                             "educational","imaging","top_of_hour")
              ),
              br(),
              actionButton("btn_save_playlist_config", "Save Config",
                           class = "btn-success", icon = icon("save")),
              br(), br(),
              verbatimTextOutput("playlist_save_status")
          ),
          box(title = "P1 Interrupt Mode", width = 4, status = "danger",
              solidHeader = TRUE,
              p(tags$small(icon("exclamation-triangle"),
                " These types immediately preempt the normal playlist. Not configurable.")),
              tags$ul(
                tags$li(strong("Priority 1"), " — tornado emergency, flash flood emergency, extreme/severe"),
                tags$li(strong("Tornado"), " — tornado warnings and watches"),
                tags$li(strong("Severe Thunderstorm"), " — severe thunderstorm warnings"),
                tags$li(strong("Hurricane / Tropical"), " — hurricane/tropical storm warnings, storm surge")
              ),
              hr(),
              p(tags$small(icon("info-circle"),
                " P1 audio also queues in the zone folder and plays in the next normal cycle."))
          )
        ),
        fluidRow(
          box(title = "Audio Queue — Current Files", width = 12, status = "info",
              solidHeader = TRUE,
              p(tags$small("Live file counts for this zone's audio folders. Refresh the page to update.")),
              DT::dataTableOutput("zone_audio_inventory")
          )
        ),
        fluidRow(
          box(title = "Zone Definitions", width = 12, status = "primary",
              solidHeader = FALSE, collapsible = TRUE, collapsed = TRUE,
              DT::dataTableOutput("zones_table")
          )
        )
      ),

      # ── Reports ─────────────────────────────────────────────────────────────
      tabItem(tabName = "reports",
        fluidRow(
          box(title = "Generate PDF Report", width = 6, status = "primary",
              solidHeader = TRUE,
              selectInput("rpt_days", "Report Period",
                choices = c("1 day" = 1, "7 days" = 7, "14 days" = 14, "30 days" = 30),
                selected = 7),
              selectInput("rpt_zone", "Zone",
                choices = c("All Florida", "North Florida", "Alachua County"),
                selected = "All Florida"),
              checkboxInput("rpt_email", "Email report after generating", value = TRUE),
              br(),
              actionButton("btn_gen_report", "Generate PDF Report",
                           class = "btn-primary btn-lg", icon = icon("file-pdf")),
              br(), br(),
              verbatimTextOutput("rpt_status")
          ),
          box(title = "Recent Reports", width = 6, status = "info",
              solidHeader = TRUE,
              DTOutput("tbl_reports"),
              br(),
              uiOutput("rpt_download_links")
          )
        ),
        fluidRow(
          box(title = "Scheduled Reports", width = 12, status = "success",
              solidHeader = TRUE,
              p(icon("clock"), strong(" Daily report runs automatically at 6:00 AM ET")),
              p("Reports are saved to: ",
                code("/home/ufuser/Fpren-main/reports/output/")),
              p("To run manually from the server:"),
              code("Rscript /home/ufuser/Fpren-main/reports/generate_and_email.R 7")
          )
        ),
        hr(),
        h3(icon("chart-line"), " Weather Trends Reports"),
        fluidRow(
          box(title = "Generate Weather Trends PDF", width = 6, status = "warning",
              solidHeader = TRUE,
              selectInput("wt_city", "City / ICAO Station",
                choices = c(
                  "Jacksonville (KJAX)"    = "KJAX",
                  "Tallahassee (KTLH)"     = "KTLH",
                  "Gainesville (KGNV)"     = "KGNV",
                  "Ocala (KOCF)"           = "KOCF",
                  "Orlando (KMCO)"         = "KMCO",
                  "Daytona Beach (KDAB)"   = "KDAB",
                  "Tampa (KTPA)"           = "KTPA",
                  "St. Petersburg (KSPG)"  = "KSPG",
                  "Sarasota (KSRQ)"        = "KSRQ",
                  "Fort Myers (KRSW)"      = "KRSW",
                  "Miami (KMIA)"           = "KMIA",
                  "Fort Lauderdale (KFLL)" = "KFLL",
                  "West Palm Beach (KPBI)" = "KPBI",
                  "Key West (KEYW)"        = "KEYW",
                  "Pensacola (KPNS)"       = "KPNS",
                  "Panama City (KECP)"     = "KECP"
                ),
                selected = "KGNV"),
              dateRangeInput("wt_dates", "Date Range",
                start = Sys.Date() - 30, end = Sys.Date(),
                min   = Sys.Date() - 90, max = Sys.Date(),
                format = "yyyy-mm-dd"),
              checkboxInput("wt_email", "Email report after generating", value = FALSE),
              br(),
              actionButton("btn_gen_wx_trend", "Generate Weather Trends PDF",
                           class = "btn-warning btn-lg", icon = icon("chart-line")),
              br(), br(),
              verbatimTextOutput("wt_status")
          ),
          box(title = "About Weather Trends Reports", width = 6, status = "info",
              solidHeader = TRUE,
              p(icon("info-circle"),
                " Weather Trends reports use historical METAR snapshots stored hourly",
                " in the ", code("weather_history"), " MongoDB collection."),
              tags$ul(
                tags$li("Temperature trend line chart over the selected period"),
                tags$li("Wind speed / direction rose chart"),
                tags$li("Humidity trend"),
                tags$li("Flight category distribution (VFR/MVFR/IFR/LIFR)"),
                tags$li("Summary statistics: min / max / avg per metric"),
                tags$li("Notable IFR/LIFR events highlighted")
              ),
              hr(),
              p(icon("clock"), strong(" History is collected hourly"),
                " via the ", code("fpren-weather-history.timer"), " systemd unit."),
              p(icon("database"), " Up to 90 days of data retained per station.")
          )
        )
      )
    )
  )
  ) # close dashboardPage
  ) # close div#main_dashboard
) # close tagList

# ── Server ────────────────────────────────────────────────────────────────────
server <- function(input, output, session) {

  # ── Auth reactive state ──────────────────────────────────────────────────────
  auth_rv <- reactiveValues(
    logged_in   = FALSE,
    username    = NULL,
    role        = NULL,
    email       = NULL,
    phone       = NULL,
    user_doc    = NULL
  )

  login_msg_rv <- reactiveVal("")

  output$is_admin <- reactive({ isTRUE(auth_rv$role == "admin") })
  outputOptions(output, "is_admin", suspendWhenHidden = FALSE)

  output$login_attempts_msg <- renderUI({
    msg <- login_msg_rv()
    if (nchar(msg) == 0) return(NULL)
    tags$div(class = "alert alert-warning", style = "margin-top:8px;", msg)
  })

  # ── Login observer ───────────────────────────────────────────────────────────
  observeEvent(input$btn_login, {
    uname <- trimws(input$login_username)
    pword <- input$login_password
    if (nchar(uname) == 0 || nchar(pword) == 0) {
      login_msg_rv("Please enter your username and password.")
      return()
    }
    col <- tryCatch(
      mongo(collection = "users", db = "weather_rss", url = MONGO_URI),
      error = function(e) NULL)
    if (is.null(col)) {
      login_msg_rv("Database unavailable. Try again later.")
      return()
    }
    user <- tryCatch({
      r <- col$find(sprintf('{"username":"%s"}', uname))
      col$disconnect()
      if (nrow(r) == 0) NULL else r[1, ]
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL)
      NULL
    })

    if (is.null(user)) {
      login_msg_rv("Invalid username or password.")
      log_audit("failed_login", uname, "anonymous", "User not found")
      return()
    }

    # Check if account is active
    if (!isTRUE(user$active)) {
      login_msg_rv("Account disabled due to inactivity. Contact lawrence.bornace@ufl.edu.")
      log_audit("failed_login", uname, uname, "Account disabled")
      return()
    }

    # Check locked_until
    now_utc <- Sys.time()
    if (!is.null(user$locked_until) && !is.na(user$locked_until)) {
      lu <- tryCatch(as.POSIXct(user$locked_until, tz = "UTC"), error = function(e) NA)
      if (!is.na(lu) && now_utc < lu) {
        login_msg_rv("Account locked. Contact lawrence.bornace@ufl.edu to unlock, or wait 24 hours.")
        log_audit("failed_login", uname, uname, "Account locked")
        return()
      } else {
        # Auto-unlock
        col2 <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
        if (!is.null(col2)) {
          tryCatch({
            col2$update(sprintf('{"username":"%s"}', uname),
                        '{"$set":{"locked_until":null,"failed_attempts":0}}')
            col2$disconnect()
          }, error=function(e) tryCatch(col2$disconnect(), error=function(e2) NULL))
        }
      }
    }

    # Check 6-month inactivity
    if (!is.null(user$last_login) && !is.na(user$last_login)) {
      ll <- tryCatch(as.POSIXct(user$last_login, tz = "UTC"), error = function(e) NA)
      if (!is.na(ll) && difftime(now_utc, ll, units = "days") > 183) {
        col3 <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
        if (!is.null(col3)) {
          tryCatch({
            col3$update(sprintf('{"username":"%s"}', uname), '{"$set":{"active":false}}')
            col3$disconnect()
          }, error=function(e) tryCatch(col3$disconnect(), error=function(e2) NULL))
        }
        login_msg_rv("Account disabled due to 6 months of inactivity. Contact lawrence.bornace@ufl.edu.")
        log_audit("account_disabled", uname, uname, "6-month inactivity")
        return()
      }
    }

    # Verify password — normalize $2b$ → $2a$ for R bcrypt compatibility
    stored_hash <- gsub("^\\$2b\\$", "$2a$", user$password)
    pw_ok <- tryCatch(bcrypt::checkpw(pword, stored_hash), error = function(e) FALSE)
    if (!pw_ok) {
      fa <- if (!is.null(user$failed_attempts) && !is.na(user$failed_attempts)) as.integer(user$failed_attempts) else 0L
      fa <- fa + 1L
      col4 <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
      if (!is.null(col4)) {
        tryCatch({
          if (fa >= 3L) {
            lock_until <- format(now_utc + 86400, "%Y-%m-%dT%H:%M:%SZ", tz="UTC")
            col4$update(sprintf('{"username":"%s"}', uname),
                        sprintf('{"$set":{"failed_attempts":%d,"locked_until":"%s"}}', fa, lock_until))
            col4$disconnect()
            login_msg_rv("Account locked. Contact lawrence.bornace@ufl.edu to unlock, or wait 24 hours.")
            log_audit("account_locked", uname, uname, paste("Locked after", fa, "failed attempts"))
          } else {
            col4$update(sprintf('{"username":"%s"}', uname),
                        sprintf('{"$set":{"failed_attempts":%d}}', fa))
            col4$disconnect()
            remaining <- 3L - fa
            login_msg_rv(paste0("Invalid password. ", remaining, " attempt",
                                if (remaining == 1) "" else "s", " remaining."))
            log_audit("failed_login", uname, uname, paste("Wrong password, attempt", fa))
          }
        }, error=function(e) tryCatch(col4$disconnect(), error=function(e2) NULL))
      } else {
        login_msg_rv("Invalid password.")
      }
      return()
    }

    # Successful login — update last_login and reset failed_attempts
    col5 <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (!is.null(col5)) {
      tryCatch({
        col5$update(sprintf('{"username":"%s"}', uname),
                    sprintf('{"$set":{"failed_attempts":0,"locked_until":null,"last_login":"%s"}}',
                            format(now_utc, "%Y-%m-%dT%H:%M:%SZ", tz="UTC")))
        col5$disconnect()
      }, error=function(e) tryCatch(col5$disconnect(), error=function(e2) NULL))
    }

    log_audit("login", uname, uname, "Successful login")
    auth_rv$logged_in <- TRUE
    auth_rv$username  <- uname
    auth_rv$role      <- if (!is.null(user$role)) as.character(user$role) else "viewer"
    auth_rv$email     <- if (!is.null(user$email)) as.character(user$email) else ""
    auth_rv$phone     <- if (!is.null(user$phone)) as.character(user$phone) else ""
    auth_rv$user_doc  <- user
    login_msg_rv("")

    # Show dashboard, hide login
    shinyjs::hide("login_screen")
    shinyjs::show("main_dashboard")

    # Post-login flow checks
    must_change <- isTRUE(user$must_change_password)
    phone_ver   <- isTRUE(user$phone_verified)
    email_ver   <- isTRUE(user$email_verified)

    if (must_change) {
      showModal(modalDialog(
        title = "Welcome! You must set a new password before continuing.",
        tags$p("Please choose a new password to continue."),
        passwordInput("new_pw1", "New Password"),
        passwordInput("new_pw2", "Confirm New Password"),
        footer = tagList(
          actionButton("btn_submit_new_pw", "Set Password", class = "btn-primary")
        ),
        easyClose = FALSE
      ))
    } else if (!phone_ver && nchar(auth_rv$phone) > 0) {
      show_phone_verify_modal(auth_rv$phone)
    } else if (!email_ver && nchar(auth_rv$email) > 0) {
      show_email_verify_modal(auth_rv$email)
    }
  })

  # ── Must-change-password modal ────────────────────────────────────────────────
  observeEvent(input$btn_submit_new_pw, {
    p1 <- input$new_pw1
    p2 <- input$new_pw2
    if (is.null(p1) || nchar(p1) < 8) {
      showNotification("Password must be at least 8 characters.", type = "error"); return()
    }
    if (p1 != p2) {
      showNotification("Passwords do not match.", type = "error"); return()
    }
    uname <- auth_rv$username
    new_hash <- bcrypt::hashpw(p1)
    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (!is.null(col)) {
      tryCatch({
        col$update(sprintf('{"username":"%s"}', uname),
                   sprintf('{"$set":{"password":"%s","must_change_password":false}}', new_hash))
        col$disconnect()
      }, error=function(e) tryCatch(col$disconnect(), error=function(e2) NULL))
    }
    log_audit("password_change", uname, uname, "First-login password change")
    removeModal()
    showNotification("Password updated successfully.", type = "message")

    # Next: phone verification
    if (!isTRUE(auth_rv$user_doc$phone_verified) && nchar(auth_rv$phone) > 0) {
      show_phone_verify_modal(auth_rv$phone)
    } else if (!isTRUE(auth_rv$user_doc$email_verified) && nchar(auth_rv$email) > 0) {
      show_email_verify_modal(auth_rv$email)
    }
  })

  # ── Phone verification helpers ─────────────────────────────────────────────
  show_phone_verify_modal <- function(phone) {
    showModal(modalDialog(
      title = paste("Verify your phone number:", phone),
      tags$p("Click 'Send Code' to receive a 6-digit verification code by SMS."),
      actionButton("btn_send_phone_code", "Send Code", class = "btn-default"),
      br(), br(),
      textInput("phone_verify_code", "Enter 6-digit code", placeholder = "000000"),
      footer = tagList(
        actionButton("btn_verify_phone", "Verify", class = "btn-primary")
      ),
      easyClose = FALSE
    ))
  }

  observeEvent(input$btn_send_phone_code, {
    uname <- auth_rv$username
    code  <- gen_code6()
    exp   <- format(Sys.time() + 600, "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (!is.null(col)) {
      tryCatch({
        col$update(sprintf('{"username":"%s"}', uname),
                   sprintf('{"$set":{"verify_code":"%s","verify_expires":"%s"}}', code, exp))
        col$disconnect()
      }, error=function(e) tryCatch(col$disconnect(), error=function(e2) NULL))
    }
    send_twilio_sms(auth_rv$phone,
                    paste("FPREN verification code:", code, "- expires in 10 minutes."))
    showNotification("SMS code sent.", type = "message")
  })

  observeEvent(input$btn_verify_phone, {
    uname   <- auth_rv$username
    entered <- trimws(input$phone_verify_code)
    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) { showNotification("DB error.", type="error"); return() }
    user <- tryCatch({
      r <- col$find(sprintf('{"username":"%s"}', uname))
      col$disconnect()
      if (nrow(r) == 0) NULL else r[1,]
    }, error=function(e) { tryCatch(col$disconnect(), error=function(e2) NULL); NULL })
    if (is.null(user)) { showNotification("User not found.", type="error"); return() }
    stored_code <- if (!is.null(user$verify_code)) as.character(user$verify_code) else ""
    stored_exp  <- if (!is.null(user$verify_expires)) as.character(user$verify_expires) else ""
    exp_time <- tryCatch(lubridate::ymd_hms(stored_exp), error=function(e) as.POSIXct(0))
    if (entered != stored_code || Sys.time() > exp_time) {
      showNotification("Invalid or expired code.", type="error"); return()
    }
    col2 <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (!is.null(col2)) {
      tryCatch({
        col2$update(sprintf('{"username":"%s"}', uname),
                    '{"$set":{"phone_verified":true,"verify_code":null,"verify_expires":null}}')
        col2$disconnect()
      }, error=function(e) tryCatch(col2$disconnect(), error=function(e2) NULL))
    }
    log_audit("phone_verified", uname, uname, "Phone verification successful")
    removeModal()
    showNotification("Phone verified!", type="message")
    if (!isTRUE(auth_rv$user_doc$email_verified) && nchar(auth_rv$email) > 0) {
      show_email_verify_modal(auth_rv$email)
    }
  })

  # ── Email verification helpers ─────────────────────────────────────────────
  show_email_verify_modal <- function(email) {
    showModal(modalDialog(
      title = paste("Verify your email:", email),
      tags$p("Click 'Send Code' to receive a 6-digit verification code by email."),
      actionButton("btn_send_email_code", "Send Code", class = "btn-default"),
      br(), br(),
      textInput("email_verify_code", "Enter 6-digit code", placeholder = "000000"),
      footer = tagList(
        actionButton("btn_verify_email", "Verify", class = "btn-primary")
      ),
      easyClose = FALSE
    ))
  }

  observeEvent(input$btn_send_email_code, {
    uname <- auth_rv$username
    code  <- gen_code6()
    exp   <- format(Sys.time() + 600, "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (!is.null(col)) {
      tryCatch({
        col$update(sprintf('{"username":"%s"}', uname),
                   sprintf('{"$set":{"verify_code":"%s","verify_expires":"%s"}}', code, exp))
        col$disconnect()
      }, error=function(e) tryCatch(col$disconnect(), error=function(e2) NULL))
    }
    send_fpren_email(auth_rv$email,
                     "FPREN Email Verification Code",
                     paste0("<h3>FPREN Email Verification</h3>",
                            "<p>Your verification code is: <strong style='font-size:24px;'>", code,
                            "</strong></p><p>This code expires in 10 minutes.</p>"))
    showNotification("Verification email sent.", type="message")
  })

  observeEvent(input$btn_verify_email, {
    uname   <- auth_rv$username
    entered <- trimws(input$email_verify_code)
    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) { showNotification("DB error.", type="error"); return() }
    user <- tryCatch({
      r <- col$find(sprintf('{"username":"%s"}', uname))
      col$disconnect()
      if (nrow(r) == 0) NULL else r[1,]
    }, error=function(e) { tryCatch(col$disconnect(), error=function(e2) NULL); NULL })
    if (is.null(user)) { showNotification("User not found.", type="error"); return() }
    stored_code <- if (!is.null(user$verify_code)) as.character(user$verify_code) else ""
    stored_exp  <- if (!is.null(user$verify_expires)) as.character(user$verify_expires) else ""
    exp_time <- tryCatch(lubridate::ymd_hms(stored_exp), error=function(e) as.POSIXct(0))
    if (entered != stored_code || Sys.time() > exp_time) {
      showNotification("Invalid or expired code.", type="error"); return()
    }
    col2 <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (!is.null(col2)) {
      tryCatch({
        col2$update(sprintf('{"username":"%s"}', uname),
                    '{"$set":{"email_verified":true,"verify_code":null,"verify_expires":null}}')
        col2$disconnect()
      }, error=function(e) tryCatch(col2$disconnect(), error=function(e2) NULL))
    }
    log_audit("email_verified", uname, uname, "Email verification successful")
    removeModal()
    showNotification("Email verified!", type="message")
    # Send welcome email
    send_fpren_email(auth_rv$email, "Welcome to FPREN Dashboard",
      paste0(
        "<h2>Welcome to the FPREN Dashboard!</h2>",
        "<p>Hello ", uname, ",</p>",
        "<p>Your account has been fully verified. You now have access to the ",
        "Florida Public Radio Emergency Network monitoring dashboard.</p>",
        "<h3>Important: Account Inactivity Policy</h3>",
        "<p>Your account will be <strong>automatically disabled after 6 months of inactivity</strong>. ",
        "To keep your account active, please log in at least once every 6 months.</p>",
        "<p>If your account is disabled, contact ",
        "<a href='mailto:lawrence.bornace@ufl.edu'>lawrence.bornace@ufl.edu</a> to restore access.</p>",
        "<p>For questions or support, please contact lawrence.bornace@ufl.edu.</p>"
      ))
  })

  # ── Forgot username/password ──────────────────────────────────────────────
  observeEvent(input$btn_forgot, {
    showModal(modalDialog(
      title = "Forgot Username or Password",
      tags$p("Enter your email address. If found, we'll send your username and a password reset code."),
      textInput("forgot_email", "Email Address", placeholder = "you@ufl.edu"),
      footer = tagList(
        modalButton("Cancel"),
        actionButton("btn_send_reset", "Send Reset Email", class = "btn-primary")
      )
    ))
  })

  observeEvent(input$btn_send_reset, {
    em <- trimws(input$forgot_email)
    if (nchar(em) == 0) { showNotification("Enter an email address.", type="error"); return() }
    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) { showNotification("DB unavailable.", type="error"); return() }
    user <- tryCatch({
      r <- col$find(sprintf('{"email":"%s"}', em))
      col$disconnect()
      if (nrow(r) == 0) NULL else r[1,]
    }, error=function(e) { tryCatch(col$disconnect(), error=function(e2) NULL); NULL })
    if (!is.null(user)) {
      code <- gen_code6()
      exp  <- format(Sys.time() + 3600, "%Y-%m-%dT%H:%M:%SZ", tz="UTC")
      col2 <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
      if (!is.null(col2)) {
        tryCatch({
          col2$update(sprintf('{"email":"%s"}', em),
                      sprintf('{"$set":{"reset_code":"%s","reset_expires":"%s"}}', code, exp))
          col2$disconnect()
        }, error=function(e) tryCatch(col2$disconnect(), error=function(e2) NULL))
      }
      uname <- as.character(user$username)
      send_fpren_email(em, "FPREN Account: Username & Password Reset",
        paste0(
          "<h3>FPREN Account Information</h3>",
          "<p>Your username is: <strong>", uname, "</strong></p>",
          "<p>Your password reset code is: <strong style='font-size:24px;'>", code, "</strong></p>",
          "<p>This code expires in 1 hour. Enter it on the login screen to reset your password.</p>",
          "<p>If you did not request this, please contact lawrence.bornace@ufl.edu immediately.</p>"
        ))
      log_audit("password_reset_request", uname, uname, paste("Reset code sent to", em))
    }
    removeModal()
    showNotification("If that email is registered, a reset code was sent.", type="message")
    # Show reset code entry modal
    showModal(modalDialog(
      title = "Enter Password Reset Code",
      tags$p("Enter the 6-digit code sent to your email, then choose a new password."),
      textInput("reset_code_input", "Reset Code", placeholder = "000000"),
      passwordInput("reset_new_pw1", "New Password"),
      passwordInput("reset_new_pw2", "Confirm New Password"),
      footer = tagList(
        modalButton("Cancel"),
        actionButton("btn_submit_reset", "Reset Password", class = "btn-primary")
      )
    ))
  })

  observeEvent(input$btn_submit_reset, {
    em   <- trimws(input$forgot_email)
    code <- trimws(input$reset_code_input)
    p1   <- input$reset_new_pw1
    p2   <- input$reset_new_pw2
    if (p1 != p2) { showNotification("Passwords do not match.", type="error"); return() }
    if (nchar(p1) < 8) { showNotification("Password must be at least 8 characters.", type="error"); return() }
    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) { showNotification("DB unavailable.", type="error"); return() }
    user <- tryCatch({
      r <- col$find(sprintf('{"email":"%s"}', em))
      col$disconnect()
      if (nrow(r) == 0) NULL else r[1,]
    }, error=function(e) { tryCatch(col$disconnect(), error=function(e2) NULL); NULL })
    if (is.null(user)) { showNotification("Email not found.", type="error"); return() }
    stored_code <- if (!is.null(user$reset_code)) as.character(user$reset_code) else ""
    stored_exp  <- if (!is.null(user$reset_expires)) as.character(user$reset_expires) else ""
    exp_time <- tryCatch(lubridate::ymd_hms(stored_exp), error=function(e) as.POSIXct(0))
    if (code != stored_code || Sys.time() > exp_time) {
      showNotification("Invalid or expired reset code.", type="error"); return()
    }
    new_hash <- bcrypt::hashpw(p1)
    col2 <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (!is.null(col2)) {
      uname <- as.character(user$username)
      tryCatch({
        col2$update(sprintf('{"email":"%s"}', em),
                    sprintf('{"$set":{"password":"%s","must_change_password":false,"reset_code":null,"reset_expires":null}}',
                            new_hash))
        col2$disconnect()
      }, error=function(e) tryCatch(col2$disconnect(), error=function(e2) NULL))
      log_audit("password_reset", uname, uname, "Password reset via email code")
    }
    removeModal()
    showNotification("Password reset successful. Please log in.", type="message")
  })

  # ── Inactivity timeout ─────────────────────────────────────────────────────
  observeEvent(input$idle_warn, {
    if (!auth_rv$logged_in) return()
    showModal(modalDialog(
      title = "Are you still there?",
      tags$p("You will be logged out in 1 minute due to inactivity."),
      footer = tagList(
        actionButton("btn_stay_active", "Yes, I'm here", class = "btn-primary")
      ),
      easyClose = FALSE
    ))
  })

  observeEvent(input$btn_stay_active, {
    removeModal()
  })

  observeEvent(input$idle_logout, {
    if (!auth_rv$logged_in) return()
    log_audit("logout", auth_rv$username %||% "unknown", auth_rv$username %||% "unknown", "Inactivity timeout")
    auth_rv$logged_in <- FALSE
    auth_rv$username  <- NULL
    auth_rv$role      <- NULL
    removeModal()
    shinyjs::hide("main_dashboard")
    shinyjs::show("login_screen")
    updateTextInput(session, "login_username", value = "")
    updateTextInput(session, "login_password", value = "")
    login_msg_rv("You were logged out due to inactivity.")
  })

  # ── Notification email config ─────────────────────────────────────────────
  notify_email_status <- reactiveVal("")
  output$cfg_notify_emails_status <- renderText({ notify_email_status() })

  # Load saved notification emails on startup
  observe({
    col <- tryCatch(mongo(collection="notification_config", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) return()
    tryCatch({
      r <- col$find('{"_id":"singleton"}')
      col$disconnect()
      if (nrow(r) > 0 && !is.null(r$notify_emails)) {
        updateTextInput(session, "cfg_notify_emails", value = as.character(r$notify_emails))
      }
    }, error=function(e) tryCatch(col$disconnect(), error=function(e2) NULL))
  })

  observeEvent(input$btn_save_notify_emails, {
    emails <- trimws(input$cfg_notify_emails)
    col <- tryCatch(mongo(collection="notification_config", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) { notify_email_status("DB unavailable."); return() }
    tryCatch({
      existing <- col$find('{"_id":"singleton"}')
      if (nrow(existing) == 0) {
        col$insert(list(`_id` = "singleton", notify_emails = emails))
      } else {
        col$update('{"_id":"singleton"}',
                   sprintf('{"$set":{"notify_emails":"%s"}}', emails))
      }
      col$disconnect()
      notify_email_status(paste("Saved at", format(Sys.time(), "%H:%M:%S")))
    }, error=function(e) {
      tryCatch(col$disconnect(), error=function(e2) NULL)
      notify_email_status(paste("Error:", conditionMessage(e)))
    })
  })

  # Auto-refresh timer
  timer <- reactiveTimer(60000)

  # ── Data loaders ────────────────────────────────────────────────────────────

  alerts_data <- reactive({
    if (input$auto_refresh) timer()
    col <- get_col("nws_alerts")
    if (is.null(col)) return(data.frame())
    tryCatch({
      df <- col$find('{}', fields = '{"alert_id":1,"event":1,"headline":1,
        "severity":1,"area_desc":1,"source":1,"sent":1,"_id":0}')
      col$disconnect()
      df
    }, error = function(e) data.frame())
  })

  airport_data <- reactive({
    if (input$auto_refresh) timer()
    col <- get_col("airport_delays")
    if (is.null(col)) return(data.frame())
    tryCatch({
      df <- col$find('{}', fields = '{"icao":1,"name":1,"state":1,
        "has_delay":1,"fetched_at":1,"_id":0}')
      col$disconnect()
      df
    }, error = function(e) data.frame())
  })

  metar_data <- reactive({
    if (input$auto_refresh) timer()
    col <- get_col("airport_metar")
    if (is.null(col)) return(data.frame())
    tryCatch({
      df <- col$find('{}', fields = '{"icaoId":1,"name":1,"temp":1,
        "wspd":1,"wdir":1,"visib":1,"fltCat":1,"obsTime":1,"_id":0}')
      col$disconnect()
      if (nrow(df) == 0) return(data.frame())
      df %>%
        rename(icao = icaoId) %>%
        mutate(
          temp_f     = if_else(!is.na(temp),
                         paste0(round(temp * 9/5 + 32), "\u00b0F"), "\u2014"),
          wind       = case_when(
            is.na(wspd) ~ "\u2014",
            wspd == 0   ~ "Calm",
            TRUE        ~ paste0(wspd, " kt / ", wdir, "\u00b0")
          ),
          visibility = if_else(!is.na(visib), paste0(visib, " mi"), "\u2014"),
          sky        = if_else(!is.na(fltCat) & nchar(as.character(fltCat)) > 0,
                         as.character(fltCat), "\u2014"),
          obs_time   = tryCatch(
            format(as.POSIXct(obsTime, tz = "UTC"), "%H:%M UTC"),
            error = function(e) "\u2014"
          )
        )
    }, error = function(e) data.frame())
  })

  # ── wx_cities: ICAO → city name + coordinates ────────────────────────────────
  WX_CITIES <- data.frame(
    icao = c("KJAX","KTLH","KGNV","KOCF","KMCO","KDAB",
             "KTPA","KSPG","KSRQ","KRSW","KMIA","KFLL",
             "KPBI","KEYW","KPNS","KECP"),
    city = c("Jacksonville","Tallahassee","Gainesville","Ocala","Orlando","Daytona Beach",
             "Tampa","St. Petersburg","Sarasota","Fort Myers","Miami","Fort Lauderdale",
             "West Palm Beach","Key West","Pensacola","Panama City"),
    lat  = c(30.49,30.40,29.69,29.17,28.43,29.18,
             27.98,27.92,27.40,26.54,25.80,26.07,
             26.68,24.56,30.47,30.36),
    lon  = c(-81.69,-84.35,-82.27,-82.22,-81.31,-81.06,
             -82.53,-82.69,-82.55,-81.76,-80.29,-80.15,
             -80.10,-81.76,-87.19,-85.80),
    stringsAsFactors = FALSE
  )

  # NWS GeoServer WMS — base reflectivity radar image (returns 200, verified 2026-04-01)
  # EPSG:4326 WMS 1.3.0 BBOX order: minLat,minLon,maxLat,maxLon
  nws_radar_url <- function(lat, lon, half_deg = 1.5, px = 420) {
    paste0(
      "https://opengeo.ncep.noaa.gov/geoserver/conus/conus_bref_qcd/ows",
      "?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap",
      "&FORMAT=image%2Fpng&TRANSPARENT=true",
      "&LAYERS=conus_bref_qcd&CRS=EPSG%3A4326",
      "&WIDTH=", px, "&HEIGHT=", px,
      "&BBOX=", lat - half_deg, ",", lon - half_deg,
      ",",      lat + half_deg, ",", lon + half_deg
    )
  }

  # Florida state-wide composite (W:700 H:600 matches ~7.5×6.5° aspect)
  FL_RADAR_URL <- paste0(
    "https://opengeo.ncep.noaa.gov/geoserver/conus/conus_bref_qcd/ows",
    "?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap",
    "&FORMAT=image%2Fpng&TRANSPARENT=true",
    "&LAYERS=conus_bref_qcd&CRS=EPSG%3A4326",
    "&WIDTH=700&HEIGHT=600",
    "&BBOX=24.5,-87.5,31.0,-80.0"
  )

  # Florida county approximate centroids (lat, lon) for NWS API lookups
  FL_COUNTY_LATLON <- data.frame(
    county = c(
      "Alachua","Baker","Bay","Bradford","Brevard","Broward","Calhoun",
      "Charlotte","Citrus","Clay","Collier","Columbia","Miami-Dade","DeSoto",
      "Dixie","Duval","Escambia","Flagler","Franklin","Gadsden","Gilchrist",
      "Glades","Gulf","Hamilton","Hardee","Hendry","Hernando","Highlands",
      "Hillsborough","Holmes","Indian River","Jackson","Jefferson","Lafayette",
      "Lake","Lee","Leon","Levy","Liberty","Madison","Manatee","Marion",
      "Martin","Monroe","Nassau","Okaloosa","Okeechobee","Orange","Osceola",
      "Palm Beach","Pasco","Pinellas","Polk","Putnam","Saint Johns",
      "Saint Lucie","Santa Rosa","Sarasota","Seminole","Sumter","Suwannee",
      "Taylor","Union","Volusia","Wakulla","Walton","Washington"),
    lat = c(
      29.67,30.33,30.21,29.94,28.23,26.15,30.41,
      26.97,28.84,29.98,26.11,30.22,25.55,27.19,
      29.68,30.33,30.55,29.47,29.80,30.59,29.72,
      26.94,29.87,30.48,27.49,26.50,28.55,27.35,
      27.90,30.87,27.74,30.83,30.52,30.07,28.76,
      26.56,30.46,29.31,30.25,30.46,27.48,29.23,
      27.09,24.70,30.52,30.74,27.24,28.45,28.06,
      26.65,28.28,27.86,27.90,29.56,29.96,
      27.35,30.76,27.22,28.66,28.69,30.18,
      30.04,29.98,29.07,30.26,30.62,30.63),
    lon = c(
      -82.49,-82.27,-85.62,-82.14,-80.72,-80.46,-85.16,
      -81.94,-82.47,-81.76,-81.39,-82.62,-80.60,-81.83,
      -83.18,-81.66,-87.35,-81.21,-84.89,-84.63,-82.74,
      -81.11,-85.23,-82.97,-81.83,-80.91,-82.41,-81.28,
      -82.33,-85.81,-80.57,-85.22,-83.81,-83.16,-81.76,
      -81.71,-84.28,-82.83,-84.88,-83.42,-82.57,-82.07,
      -80.41,-81.52,-81.60,-86.49,-80.88,-81.32,-81.26,
      -80.25,-82.40,-82.77,-81.70,-81.68,-81.41,
      -80.40,-86.92,-82.48,-81.26,-82.07,-83.14,
      -83.61,-82.39,-81.24,-84.39,-86.17,-85.67),
    stringsAsFactors = FALSE
  )

  # Call NWS API for a 7-day forecast given lat/lon
  # Returns a list of period data.frames or NULL on error
  nws_get_forecast <- function(lat, lon) {
    ua <- httr::user_agent("FPREN-Dashboard/1.0 (fpren@ufl.edu)")
    pts_url <- sprintf("https://api.weather.gov/points/%.4f,%.4f", lat, lon)
    r1 <- tryCatch(
      httr::GET(pts_url, ua, httr::timeout(10)),
      error = function(e) NULL)
    if (is.null(r1) || httr::status_code(r1) != 200) return(NULL)
    pts <- tryCatch(httr::content(r1, as = "parsed"), error = function(e) NULL)
    if (is.null(pts$properties$forecastHourly)) return(NULL)
    fcst_url <- pts$properties$forecast
    r2 <- tryCatch(
      httr::GET(fcst_url, ua, httr::timeout(10)),
      error = function(e) NULL)
    if (is.null(r2) || httr::status_code(r2) != 200) return(NULL)
    fcst <- tryCatch(httr::content(r2, as = "parsed"), error = function(e) NULL)
    if (is.null(fcst$properties$periods)) return(NULL)
    periods <- fcst$properties$periods
    office_name <- paste0(pts$properties$relativeLocation$properties$city,
                          ", ", pts$properties$relativeLocation$properties$state)
    list(location = office_name, periods = periods)
  }

  wx_cities_timer <- reactiveTimer(900000)  # 15 minutes

  wx_cities_data <- reactive({
    wx_cities_timer()
    input$btn_wx_refresh
    icao_list <- paste0('["', paste(WX_CITIES$icao, collapse='","'), '"]')
    query <- paste0('{"icaoId":{"$in":', icao_list, '}}')
    col <- get_col("airport_metar")
    if (is.null(col)) return(data.frame())
    tryCatch({
      df <- col$find(query, fields = '{"icaoId":1,"name":1,"temp":1,"dewp":1,
        "wspd":1,"wdir":1,"visib":1,"fltCat":1,"obsTime":1,
        "wxString":1,"clouds":1,"rhum":1,"_id":0}')
      col$disconnect()
      if (nrow(df) == 0) return(data.frame())
      # Merge with city names
      df <- df %>% rename(icao = icaoId) %>%
        left_join(WX_CITIES, by = "icao")
      # Use city name if available, fall back to station name
      df$display_name <- ifelse(!is.na(df$city), df$city, df$name)
      # Ensure fltCat is character
      df$fltCat <- as.character(df$fltCat)
      df$fltCat[is.na(df$fltCat) | df$fltCat == ""] <- "UNK"
      df
    }, error = function(e) data.frame())
  })

  # ── ZIP code forecast ────────────────────────────────────────────────────────

  wx_zip_error_rv    <- reactiveVal("")
  wx_zip_forecast_rv <- reactiveVal(NULL)   # list(location, periods, county, metar)

  observeEvent(input$btn_wx_forecast, {
    wx_zip_error_rv("")
    wx_zip_forecast_rv(NULL)
    z <- trimws(input$wx_zip)
    if (!grepl("^\\d{5}$", z)) {
      wx_zip_error_rv("Invalid format — please enter exactly 5 digits.")
      return()
    }
    n <- as.integer(z)
    if (n < 32004L || n > 34997L) {
      wx_zip_error_rv(paste0(
        "ZIP code ", z, " is not a Florida ZIP code.",
        " Florida ZIPs range from 32004 to 34997."))
      return()
    }
    county <- zip_to_florida_county(z)
    if (is.na(county)) {
      wx_zip_error_rv(paste0("ZIP code ", z, " could not be matched to a Florida county."))
      return()
    }
    # Look up county centroid
    idx <- which(FL_COUNTY_LATLON$county == county)
    if (length(idx) == 0) {
      wx_zip_error_rv(paste0("No coordinates on file for ", county, " County."))
      return()
    }
    lat <- FL_COUNTY_LATLON$lat[idx[1]]
    lon <- FL_COUNTY_LATLON$lon[idx[1]]

    # Fetch NWS 7-day forecast
    wx_zip_error_rv(paste0("Fetching NWS forecast for ", county, " County\u2026"))
    result <- nws_get_forecast(lat, lon)
    if (is.null(result)) {
      wx_zip_error_rv(paste0(
        "NWS API unavailable or no data for ", county, " County (",
        lat, ", ", lon, "). Try again in a moment."))
      return()
    }

    # Pull nearest ASOS obs from airport_metar (best match ICAO for county)
    zone <- COUNTY_TO_ZONE[county]
    # Map zone → representative ICAO
    zone_icao <- c(
      gainesville="KGNV", jacksonville="KJAX", orlando="KMCO",
      tampa="KTPA", miami="KMIA", north_florida="KTLH",
      central_florida="KMCO", south_florida="KRSW", all_florida="KMCO"
    )
    icao_guess <- if (!is.na(zone) && zone %in% names(zone_icao)) zone_icao[zone] else "KGNV"
    metar_row <- NULL
    col <- get_col("airport_metar")
    if (!is.null(col)) {
      metar_row <- tryCatch({
        r <- col$find(sprintf('{"icaoId":"%s"}', icao_guess),
                      fields='{"icaoId":1,"temp":1,"dewp":1,"wspd":1,"wdir":1,
                               "visib":1,"fltCat":1,"obsTime":1,"wxString":1,"_id":0}',
                      limit = 1)
        col$disconnect()
        if (nrow(r) > 0) r else NULL
      }, error = function(e) { tryCatch(col$disconnect(), error=function(e2) NULL); NULL })
    }

    # Find nearest WX_CITIES city for radar image
    dists <- sqrt((WX_CITIES$lat - lat)^2 + (WX_CITIES$lon - lon)^2)
    ni <- which.min(dists)
    radar_city <- list(city = WX_CITIES$city[ni],
                       lat  = WX_CITIES$lat[ni],
                       lon  = WX_CITIES$lon[ni])

    wx_zip_error_rv("")
    wx_zip_forecast_rv(list(
      location   = result$location,
      county     = county,
      periods    = result$periods,
      metar      = metar_row,
      radar_city = radar_city
    ))
  })

  observeEvent(input$btn_wx_clear, {
    wx_zip_error_rv("")
    wx_zip_forecast_rv(NULL)
    updateTextInput(session, "wx_zip", value = "")
  })

  output$wx_zip_error_ui <- renderUI({
    msg <- wx_zip_error_rv()
    if (nchar(msg) == 0) return(NULL)
    # Progress style while fetching
    is_progress <- grepl("Fetching", msg)
    bg <- if (is_progress) "#2980b9" else "#c0392b"
    div(style = paste0(
          "background:", bg, "; color:white; padding:10px 15px;",
          "border-radius:4px; margin-bottom:12px; font-weight:bold;"),
      icon(if (is_progress) "spinner" else "exclamation-circle"), " ", msg)
  })

  output$wx_zip_forecast_ui <- renderUI({
    data <- wx_zip_forecast_rv()
    if (is.null(data)) return(NULL)

    # Current conditions from METAR
    cur_html <- NULL
    m <- data$metar
    if (!is.null(m) && nrow(m) > 0) {
      temp_f <- if (!is.na(m$temp[1])) paste0(round(m$temp[1]*9/5+32), "\u00b0F") else "\u2014"
      wind_s <- if (!is.na(m$wspd[1]) && m$wspd[1] > 0)
        paste0(m$wspd[1], " kt / ", m$wdir[1], "\u00b0") else "Calm"
      vis_s  <- if (!is.na(m$visib[1])) paste0(m$visib[1], " mi") else "\u2014"
      cat_s  <- if (!is.na(m$fltCat[1])) as.character(m$fltCat[1]) else "\u2014"
      wx_s   <- if ("wxString" %in% names(m) && !is.na(m$wxString[1]) && nchar(m$wxString[1])>0)
                  m$wxString[1] else ""
      cur_html <- div(class = "zip-panel zip-cur",
        strong("Current conditions (nearest ASOS): "),
        temp_f, " | Wind: ", wind_s, " | Vis: ", vis_s,
        " | Flight cat: ", strong(cat_s),
        if (nchar(wx_s)>0) paste0(" | ", wx_s)
      )
    }

    # Build day cards from NWS periods (daytime periods only, up to 7)
    periods <- data$periods
    day_cards <- lapply(periods, function(p) {
      name <- p$name %||% "?"
      is_day <- isTRUE(p$isDaytime)
      temp_val <- if (!is.null(p$temperature)) paste0(p$temperature, "\u00b0F") else "\u2014"
      precip <- if (!is.null(p$probabilityOfPrecipitation$value) &&
                    !is.na(p$probabilityOfPrecipitation$value))
                  paste0(p$probabilityOfPrecipitation$value, "% precip") else ""
      wind_sp <- paste0(p$windSpeed %||% "", " ", p$windDirection %||% "")
      desc_s  <- p$shortForecast %||% ""
      div(class = "zip-day-card",
        div(class = "day-name", name),
        div(class = if (is_day) "day-hi" else "day-lo", temp_val),
        div(class = "day-precip", precip),
        div(class = "day-wind", wind_sp),
        div(class = "day-desc", desc_s)
      )
    })

    # Radar image for nearest WX_CITIES city
    rc <- data$radar_city
    radar_img <- if (!is.null(rc)) {
      r_url <- nws_radar_url(rc$lat, rc$lon)
      div(class = "zip-radar-wrap",
        tags$img(
          id         = "zip-city-radar",
          src        = paste0(r_url, "&t=", as.integer(Sys.time())),
          `data-src` = r_url,
          alt        = paste0(rc$city, " area radar"),
          style      = "max-width:420px;"
        ),
        div(class = "fl-radar-ts",
          icon("satellite-dish"), " Nearest station: ", rc$city,
          " \u2014 NWS NEXRAD \u2014 auto-refreshes every 5 min")
      )
    } else NULL

    div(class = "zip-panel",
      fluidRow(
        column(8,
          h4(icon("map-marker-alt"), " ", data$location, " — ", data$county, " County"),
          if (!is.null(cur_html)) cur_html,
          h5(icon("calendar-alt"), " 7-Day Forecast"),
          div(class = "zip-days-scroll", day_cards)
        ),
        column(4, radar_img)
      )
    )
  })

  radar_refresh_timer <- reactiveTimer(300000)  # 5 minutes

  output$fl_state_radar_map <- renderLeaflet({
    leaflet(options = leafletOptions(zoomControl = TRUE)) %>%
      setView(lng = -83.5, lat = 27.5, zoom = 6) %>%
      addProviderTiles(providers$CartoDB.Positron) %>%
      addWMSTiles(
        baseUrl = "https://opengeo.ncep.noaa.gov/geoserver/conus/conus_bref_qcd/ows",
        layers  = "conus_bref_qcd",
        options = WMSTileOptions(
          format      = "image/png",
          transparent = TRUE,
          version     = "1.3.0",
          opacity     = 0.8
        )
      )
  })

  observeEvent(radar_refresh_timer(), {
    leafletProxy("fl_state_radar_map") %>%
      clearTiles() %>%
      addProviderTiles(providers$CartoDB.Positron) %>%
      addWMSTiles(
        baseUrl = "https://opengeo.ncep.noaa.gov/geoserver/conus/conus_bref_qcd/ows",
        layers  = "conus_bref_qcd",
        options = WMSTileOptions(
          format      = "image/png",
          transparent = TRUE,
          version     = "1.3.0",
          opacity     = 0.8
        )
      )
  }, ignoreInit = TRUE)

  # ── traffic data ─────────────────────────────────────────────────────────────
  traffic_timer <- reactiveTimer(120000)  # 2 minutes

  traffic_data <- reactive({
    traffic_timer()
    input$btn_traffic_refresh
    col <- get_col("fl_traffic")
    if (is.null(col)) return(data.frame())
    tryCatch({
      df <- col$find('{}', fields = '{"incident_id":1,"county":1,"road":1,
        "direction":1,"type":1,"event_subtype":1,"description":1,
        "severity":1,"is_full_closure":1,"major_event":1,
        "last_updated":1,"_id":0}')
      col$disconnect()
      df
    }, error = function(e) data.frame())
  })

  wav_data <- reactive({
    if (input$auto_refresh) timer()
    col <- get_col("zone_alert_wavs")
    if (is.null(col)) return(data.frame())
    tryCatch({
      df <- col$find('{}', fields = '{"source_type":1,"zone":1,"event":1,
        "tts_engine":1,"generated_at":1,"wav_path":1,"_id":0}',
        sort = '{"generated_at":-1}', limit = 50)
      col$disconnect()
      df
    }, error = function(e) data.frame())
  })

  feed_data <- reactive({
    if (input$auto_refresh) timer()
    col <- get_col("feed_status")
    if (is.null(col)) return(data.frame())
    tryCatch({
      df <- col$find('{}', fields = '{"filename":1,"status":1,
        "last_checked":1,"_id":0}')
      col$disconnect()
      df
    }, error = function(e) data.frame())
  })

  # ── Overview boxes ──────────────────────────────────────────────────────────

  output$box_fl_alerts <- renderValueBox({
    df <- alerts_data()
    n  <- if (nrow(df) == 0) 0 else nrow(df)
    valueBox(n, "FL Alerts Active", icon = icon("exclamation-triangle"),
             color = if (n > 0) "red" else "green")
  })

  output$box_county_alerts <- renderValueBox({
    df <- alerts_data()
    n  <- if (nrow(df) == 0 || !"source" %in% names(df)) 0
          else sum(grepl("^county_nws:", df$source, ignore.case = TRUE), na.rm = TRUE)
    valueBox(n, "County Alerts", icon = icon("map-marker-alt"),
             color = if (n > 0) "orange" else "green")
  })

  output$box_airport_delays <- renderValueBox({
    df <- airport_data()
    n  <- if (nrow(df) == 0 || !"has_delay" %in% names(df)) 0
          else sum(df$has_delay == TRUE, na.rm = TRUE)
    valueBox(n, "Airport Delays", icon = icon("plane"),
             color = if (n > 0) "yellow" else "green")
  })

  output$box_wavs_generated <- renderValueBox({
    df <- wav_data()
    valueBox(nrow(df), "Recent Audio Files", icon = icon("volume-up"),
             color = "blue")
  })

  output$tbl_overview_alerts <- renderDT({
    df <- alerts_data()
    if (nrow(df) == 0) return(datatable(data.frame(Message = "No active alerts")))
    df <- df %>% select(any_of(c("event","severity","area_desc","source","sent")))
    datatable(df, options = list(pageLength = 5, scrollX = TRUE),
              rownames = FALSE)
  })

  output$txt_tts_engine <- renderText({ "ElevenLabs via LiteLLM" })

  output$txt_heartbeat <- renderText({
    path <- "/home/ufuser/Fpren-main/watchdog.heartbeat"
    if (file.exists(path)) {
      mtime <- file.mtime(path)
      age   <- round(as.numeric(difftime(Sys.time(), mtime, units = "mins")), 1)
      paste0(format(mtime, "%Y-%m-%d %H:%M:%S"), "\n(", age, " minutes ago)")
    } else {
      "Heartbeat file not found"
    }
  })

  output$txt_mongo_status <- renderText({
    col <- get_col("nws_alerts")
    if (is.null(col)) "MongoDB: OFFLINE" else {
      col$disconnect()
      "MongoDB: ONLINE"
    }
  })

  # ── FL Alerts tab ──────────────────────────────────────────────────────────

  output$tbl_alerts <- renderDT({
    df <- alerts_data()
    if (nrow(df) == 0) return(datatable(data.frame(Message = "No alerts found")))

    if (input$alert_severity != "All" && "severity" %in% names(df))
      df <- df %>% filter(tolower(severity) == tolower(input$alert_severity))
    if (input$alert_source != "All" && "source" %in% names(df))
      df <- df %>% filter(source == input$alert_source)

    df <- df %>% select(any_of(c("event","severity","headline","area_desc",
                                  "source","sent")))
    datatable(df, options = list(pageLength = 10, scrollX = TRUE),
              rownames = FALSE) %>%
      formatStyle("severity",
        backgroundColor = styleEqual(
          c("Extreme","Severe","Moderate"),
          c("#f56954",  "#f39c12", "#f0ad4e")))
  })

  observeEvent(input$btn_refresh_alerts, { alerts_data() })

  # ── Weather Conditions tab ──────────────────────────────────────────────────

  MAJOR_ICAOS <- c("KJAX", "KGNV", "KTPA", "KMCO", "KMIA")

  show_all_cities <- reactiveVal(FALSE)

  observeEvent(input$btn_wx_toggle_cities, {
    show_all_cities(!show_all_cities())
    updateActionButton(session, "btn_wx_toggle_cities",
      label = if (show_all_cities()) "Show Major Cities" else "Show All Cities")
  })

  output$wx_cities_grid <- renderUI({
    df <- wx_cities_data()
    if (!show_all_cities()) df <- df[df$icao %in% MAJOR_ICAOS, ]
    if (nrow(df) == 0) {
      return(fluidRow(column(12,
        div(style = "padding: 30px; text-align: center; color: #666;",
            icon("exclamation-circle"), " No METAR data available — ASOS stations may not have reported yet."))))
    }

    # Helper: feels-like temperature (heat index / wind chill approximation)
    feels_like <- function(temp_c, dewp_c, wspd_kt) {
      if (is.na(temp_c)) return(NA_real_)
      temp_f <- temp_c * 9/5 + 32
      # Wind chill when cold
      if (!is.na(wspd_kt) && temp_f <= 50 && wspd_kt > 3) {
        wspd_mph <- wspd_kt * 1.15078
        wc <- 35.74 + 0.6215*temp_f - 35.75*(wspd_mph^0.16) + 0.4275*temp_f*(wspd_mph^0.16)
        return(round(wc))
      }
      # Heat index when warm and humid
      if (!is.na(dewp_c) && temp_f >= 80) {
        rh <- 100 * exp((17.625 * dewp_c) / (243.04 + dewp_c)) /
                   exp((17.625 * temp_c) / (243.04 + temp_c))
        hi <- -42.379 + 2.04901523*temp_f + 10.14333127*rh -
              0.22475541*temp_f*rh - 0.00683783*temp_f^2 -
              0.05481717*rh^2 + 0.00122874*temp_f^2*rh +
              0.00085282*temp_f*rh^2 - 0.00000199*temp_f^2*rh^2
        return(round(hi))
      }
      round(temp_f)
    }

    cat_class <- function(cat) {
      switch(toupper(trimws(cat)),
        "VFR"  = "vfr",
        "MVFR" = "mvfr",
        "IFR"  = "ifr",
        "LIFR" = "lifr",
        "unknown"
      )
    }

    # Order cities by WX_CITIES order
    order_map <- setNames(seq_len(nrow(WX_CITIES)), WX_CITIES$icao)
    df$sort_order <- order_map[df$icao]
    df <- df[order(df$sort_order, na.last = TRUE), ]

    cards <- lapply(seq_len(nrow(df)), function(i) {
      row <- df[i, ]
      temp_f <- if (!is.na(row$temp)) paste0(round(row$temp * 9/5 + 32), "\u00b0F") else "\u2014"
      fl_f <- feels_like(row$temp, if ("dewp" %in% names(row)) row$dewp else NA,
                         if (!is.na(row$wspd)) row$wspd else NA)
      feels_str <- if (!is.na(fl_f)) paste0("Feels like ", fl_f, "\u00b0F") else ""
      wind_str <- if (!is.na(row$wspd) && row$wspd > 0)
        paste0(row$wspd, " kt / ", row$wdir, "\u00b0")
      else if (!is.na(row$wspd) && row$wspd == 0) "Calm" else "\u2014"
      # Humidity from rhum field or computed from dewpoint
      hum_str <- if ("rhum" %in% names(row) && !is.na(row$rhum)) {
        paste0(round(row$rhum), "% RH")
      } else if ("dewp" %in% names(row) && !is.na(row$dewp) && !is.na(row$temp)) {
        rh <- round(100 * exp((17.625 * row$dewp) / (243.04 + row$dewp)) /
                          exp((17.625 * row$temp) / (243.04 + row$temp)))
        paste0(rh, "% RH")
      } else "\u2014"
      vis_str <- if (!is.na(row$visib)) paste0(row$visib, " mi") else "\u2014"
      wx_desc <- if ("wxString" %in% names(row) && !is.na(row$wxString) && nchar(row$wxString) > 0)
        row$wxString else ""
      obs_str <- tryCatch(
        format(as.POSIXct(row$obsTime, tz = "UTC"), "%H:%M UTC"),
        error = function(e) "\u2014"
      )
      cat <- if (!is.na(row$fltCat)) row$fltCat else "UNK"
      css_class <- cat_class(cat)

      column(3,
        div(class = paste("wx-card", css_class),
          div(class = "wx-city", row$display_name),
          div(class = "wx-cat",  cat),
          div(class = "wx-temp", temp_f),
          div(class = "wx-feels", feels_str),
          if (nchar(wx_desc) > 0) div(class = "wx-desc", wx_desc),
          div(class = "wx-detail",
            icon("wind"), wind_str, tags$br(),
            icon("tint"), hum_str, tags$br(),
            icon("eye"),  vis_str),
          div(class = "wx-time", icon("clock"), " Obs: ", obs_str)
        )
      )
    })

    # Split into rows of 4
    rows <- lapply(
      seq(1, length(cards), by = 4),
      function(start) {
        chunk <- cards[start:min(start+3, length(cards))]
        do.call(fluidRow, chunk)
      }
    )
    do.call(tagList, rows)
  })

  # ── Traffic Alerts tab ──────────────────────────────────────────────────────

  # Populate filter dropdowns from data
  observe({
    df <- traffic_data()
    if (nrow(df) == 0) return()
    counties <- sort(unique(df$county[!is.na(df$county)]))
    updateSelectInput(session, "traffic_county",
      choices = c("All Counties" = "", counties))
    types <- sort(unique(df$type[!is.na(df$type)]))
    updateSelectInput(session, "traffic_type",
      choices = c("All Types" = "", types))
  })

  traffic_filtered <- reactive({
    df <- traffic_data()
    if (nrow(df) == 0) return(df)
    if (!is.null(input$traffic_county) && nchar(input$traffic_county) > 0)
      df <- df %>% filter(county == input$traffic_county)
    if (!is.null(input$traffic_severity) && nchar(input$traffic_severity) > 0)
      df <- df %>% filter(tolower(severity) == tolower(input$traffic_severity))
    if (!is.null(input$traffic_type) && nchar(input$traffic_type) > 0)
      df <- df %>% filter(type == input$traffic_type)
    df
  })

  output$box_traffic_total <- renderValueBox({
    n <- nrow(traffic_data())
    valueBox(n, "Total Incidents", icon = icon("car-crash"),
             color = if (n > 0) "orange" else "green")
  })

  output$box_traffic_major <- renderValueBox({
    df <- traffic_data()
    n  <- if (nrow(df) == 0) 0 else sum(tolower(df$severity) == "major", na.rm = TRUE)
    valueBox(n, "Major Incidents", icon = icon("exclamation-triangle"),
             color = if (n > 0) "red" else "green")
  })

  output$box_traffic_closures <- renderValueBox({
    df <- traffic_data()
    n  <- if (nrow(df) == 0) 0 else sum(df$is_full_closure == TRUE, na.rm = TRUE)
    valueBox(n, "Full Closures", icon = icon("road"),
             color = if (n > 0) "red" else "green")
  })

  output$box_traffic_counties <- renderValueBox({
    df <- traffic_data()
    n  <- if (nrow(df) == 0) 0 else length(unique(df$county[!is.na(df$county)]))
    valueBox(n, "Counties Affected", icon = icon("map"),
             color = "blue")
  })

  output$tbl_traffic <- renderDT({
    df <- traffic_filtered()
    if (nrow(df) == 0)
      return(datatable(data.frame(Message = "No traffic incidents found")))
    display <- df %>%
      mutate(
        Full_Closure = ifelse(is_full_closure == TRUE, "Yes", "No")
      ) %>%
      select(any_of(c("severity","county","road","direction",
                       "type","description","last_updated","Full_Closure"))) %>%
      rename_with(~ c("Severity","County","Road","Direction",
                       "Type","Description","Last Updated","Full Closure")[
                        seq_along(.)], everything())
    datatable(display,
              options = list(pageLength = 20, scrollX = TRUE,
                             columnDefs = list(
                               list(width = "220px", targets = 5)  # Description col
                             )),
              rownames = FALSE) %>%
      formatStyle("Severity",
        backgroundColor = styleEqual(c("Major","Minor"),
                                     c("#c0392b","#e67e22")),
        color = styleEqual(c("Major","Minor"), c("white","white")))
  })

  # ── Traffic Analysis tab ─────────────────────────────────────────────────────

  ta_raw <- reactive({
    traffic_data()   # reuse existing traffic reactive (auto-refreshes every 2 min)
  })

  # Populate filter dropdowns from live data
  observe({
    df <- ta_raw()
    if (nrow(df) == 0) return()
    roads <- sort(unique(df$road[!is.na(df$road) & df$road != ""]))
    types <- sort(unique(df$type[!is.na(df$type) & df$type != ""]))
    dists <- sort(unique(df$dot_district[!is.na(df$dot_district) & df$dot_district != ""]))
    updateSelectInput(session, "ta_road",     choices = c("All Roads" = "", roads))
    updateSelectInput(session, "ta_type",     choices = c("All Types" = "", types))
    updateSelectInput(session, "ta_district", choices = c("All Districts" = "", dists))
  })

  ta_filtered <- reactive({
    df <- ta_raw()
    if (input$ta_road     != "") df <- df %>% filter(road        == input$ta_road)
    if (input$ta_type     != "") df <- df %>% filter(type        == input$ta_type)
    if (input$ta_severity != "") df <- df %>% filter(severity    == input$ta_severity)
    if (input$ta_district != "") df <- df %>% filter(dot_district == input$ta_district)
    df
  })

  output$ta_box_total <- renderValueBox({
    valueBox(nrow(ta_filtered()), "Matching Incidents",
             icon = icon("car"), color = "blue")
  })
  output$ta_box_major <- renderValueBox({
    n <- sum(ta_filtered()$severity %in% c("Major","Intermediate"), na.rm = TRUE)
    valueBox(n, "Major / Intermediate", icon = icon("exclamation-triangle"), color = "red")
  })
  output$ta_box_closures <- renderValueBox({
    n <- sum(ta_filtered()$is_full_closure == TRUE, na.rm = TRUE)
    valueBox(n, "Full Closures", icon = icon("road"), color = "orange")
  })
  output$ta_box_counties <- renderValueBox({
    n <- length(unique(ta_filtered()$county[!is.na(ta_filtered()$county)]))
    valueBox(n, "Counties Affected", icon = icon("map-marker-alt"), color = "green")
  })

  output$ta_plot_roads <- plotly::renderPlotly({
    df <- ta_filtered()
    if (nrow(df) == 0) return(plotly::plot_ly() %>% plotly::layout(title = "No data"))
    counts <- df %>%
      filter(!is.na(road), road != "") %>%
      count(road, sort = TRUE) %>%
      slice_head(n = 20) %>%
      mutate(road = forcats::fct_reorder(road, n))
    plotly::plot_ly(counts, x = ~n, y = ~road, type = "bar", orientation = "h",
                    marker = list(color = "#3498db"),
                    hovertemplate = "%{y}: %{x} incidents<extra></extra>") %>%
      plotly::layout(
        xaxis = list(title = "Incidents"),
        yaxis = list(title = ""),
        margin = list(l = 160)
      )
  })

  output$ta_plot_types <- plotly::renderPlotly({
    df <- ta_filtered()
    if (nrow(df) == 0) return(plotly::plot_ly() %>% plotly::layout(title = "No data"))
    counts <- df %>%
      filter(!is.na(type), type != "") %>%
      count(type, sort = TRUE)
    plotly::plot_ly(counts, labels = ~type, values = ~n, type = "pie",
                    textinfo = "label+percent",
                    hovertemplate = "%{label}: %{value} incidents<extra></extra>") %>%
      plotly::layout(showlegend = TRUE,
                     legend = list(orientation = "v"))
  })

  output$ta_plot_counties <- plotly::renderPlotly({
    df <- ta_filtered()
    if (nrow(df) == 0) return(plotly::plot_ly() %>% plotly::layout(title = "No data"))
    counts <- df %>%
      filter(!is.na(county), county != "") %>%
      count(county, sort = TRUE) %>%
      slice_head(n = 25) %>%
      mutate(county = forcats::fct_reorder(county, n))
    plotly::plot_ly(counts, x = ~n, y = ~county, type = "bar", orientation = "h",
                    marker = list(color = ~n, colorscale = "YlOrRd", showscale = TRUE),
                    hovertemplate = "%{y} County: %{x} incidents<extra></extra>") %>%
      plotly::layout(
        xaxis = list(title = "Incidents"),
        yaxis = list(title = ""),
        margin = list(l = 130)
      )
  })

  output$ta_plot_severity <- plotly::renderPlotly({
    df <- ta_filtered()
    if (nrow(df) == 0) return(plotly::plot_ly() %>% plotly::layout(title = "No data"))
    counts <- df %>%
      filter(!is.na(road), road != "", !is.na(severity)) %>%
      count(road, severity) %>%
      group_by(road) %>%
      mutate(total = sum(n)) %>%
      ungroup() %>%
      filter(dense_rank(desc(total)) <= 15) %>%
      mutate(road = forcats::fct_reorder(road, total))
    sev_colors <- c(
      "Major"        = "#e74c3c",
      "Intermediate" = "#e67e22",
      "Minor"        = "#f1c40f",
      "N/A"          = "#95a5a6"
    )
    plotly::plot_ly(counts, x = ~n, y = ~road, color = ~severity,
                    colors = sev_colors,
                    type = "bar", orientation = "h",
                    hovertemplate = "%{y} — %{data.name}: %{x}<extra></extra>") %>%
      plotly::layout(
        barmode = "stack",
        xaxis = list(title = "Incidents"),
        yaxis = list(title = ""),
        legend = list(title = list(text = "Severity")),
        margin = list(l = 160)
      )
  })

  output$ta_map <- leaflet::renderLeaflet({
    df <- ta_filtered()
    base_map <- leaflet::leaflet() %>%
      leaflet::addProviderTiles(leaflet::providers$CartoDB.Positron) %>%
      leaflet::setView(lng = -83.5, lat = 27.8, zoom = 7)
    if (nrow(df) == 0) return(base_map)

    county_counts <- df %>%
      filter(!is.na(county), county != "") %>%
      count(county, name = "incidents")
    map_df <- FL_COUNTY_LATLON %>%
      left_join(county_counts, by = "county") %>%
      filter(!is.na(incidents))
    if (nrow(map_df) == 0) return(base_map)

    # Top incidents by type per county for popup detail
    top_types <- df %>%
      filter(!is.na(county), county != "", !is.na(type)) %>%
      count(county, type, sort = TRUE) %>%
      group_by(county) %>%
      slice_head(n = 3) %>%
      summarise(type_summary = paste(paste0(type, " (", n, ")"), collapse = "<br>"),
                .groups = "drop")
    map_df <- map_df %>% left_join(top_types, by = "county")

    pal <- leaflet::colorNumeric(
      palette = c("#ffffb2","#fecc5c","#fd8d3c","#f03b20","#bd0026"),
      domain  = map_df$incidents
    )
    radius_scale <- function(n) scales::rescale(sqrt(n), to = c(8, 45))

    base_map %>%
      leaflet::addCircleMarkers(
        data        = map_df,
        lat         = ~lat,
        lng         = ~lon,
        radius      = ~radius_scale(incidents),
        color       = "white",
        weight      = 1,
        fillColor   = ~pal(incidents),
        fillOpacity = 0.85,
        popup       = ~paste0(
          "<b>", county, " County</b><br>",
          "<b>", incidents, " incidents</b><br><hr>",
          "<small>", ifelse(is.na(type_summary), "", type_summary), "</small>"
        ),
        label       = ~paste0(county, ": ", incidents, " incidents")
      ) %>%
      leaflet::addLegend(
        position = "bottomright",
        pal      = pal,
        values   = map_df$incidents,
        title    = "Incidents",
        opacity  = 0.85
      )
  })

  output$ta_table <- renderDT({
    df <- ta_filtered()
    if (nrow(df) == 0)
      return(datatable(data.frame(Message = "No data matching filters")))
    display <- df %>%
      select(any_of(c("severity","county","road","direction","type",
                       "is_full_closure","dot_district","description","last_updated"))) %>%
      rename_with(~ c("Severity","County","Road","Direction","Type",
                       "Full Closure","District","Description","Last Updated")[
                        seq_along(.)], everything())
    datatable(display,
              options = list(pageLength = 25, scrollX = TRUE,
                             columnDefs = list(list(width = "200px", targets = 7))),
              rownames = FALSE) %>%
      formatStyle("Severity",
        backgroundColor = styleEqual(
          c("Major","Intermediate","Minor"),
          c("#c0392b","#e67e22","#f39c12")),
        color = styleEqual(c("Major","Intermediate","Minor"),
                           c("white","white","white")))
  })

  output$ta_download <- downloadHandler(
    filename = function() paste0("fpren_traffic_analysis_", Sys.Date(), ".csv"),
    content  = function(file) write.csv(ta_filtered(), file, row.names = FALSE)
  )

  ta_export_status <- reactiveVal("")
  output$ta_export_status <- renderText({ ta_export_status() })

  # Build a human-readable filter summary string
  ta_filter_label <- reactive({
    parts <- c(
      if (input$ta_road     != "") paste0("Road: ",     input$ta_road),
      if (input$ta_type     != "") paste0("Type: ",     input$ta_type),
      if (input$ta_severity != "") paste0("Severity: ", input$ta_severity),
      if (input$ta_district != "") paste0("District: ", input$ta_district)
    )
    if (length(parts) == 0) "No filters applied (all incidents)" else paste(parts, collapse=" | ")
  })

  # Shared PDF render helper — returns output file path or stops with error
  ta_render_pdf <- function() {
    df <- ta_filtered()
    if (nrow(df) == 0) stop("No data matches the current filters — nothing to export.")
    output_dir  <- "/home/ufuser/Fpren-main/reports/output"
    dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)
    timestamp   <- format(Sys.time(), "%Y%m%d_%H%M")
    output_file <- file.path(output_dir, paste0("traffic_analysis_", timestamp, ".pdf"))
    # Save filtered data to a temp RDS so the Rmd can read it
    rds_path <- tempfile(fileext = ".rds")
    saveRDS(df, rds_path)
    withr::with_dir(tempdir(), rmarkdown::render(
      input             = "/home/ufuser/Fpren-main/reports/traffic_analysis_report.Rmd",
      output_file       = output_file,
      intermediates_dir = tempdir(),
      params            = list(data_rds = rds_path,
                               filters  = ta_filter_label(),
                               date     = format(Sys.Date(), "%Y-%m-%d")),
      quiet = TRUE
    ))
    unlink(rds_path)
    output_file
  }

  observeEvent(input$btn_ta_pdf, {
    ta_export_status("Generating PDF\u2026 (30\u201360 seconds)")
    tryCatch({
      pdf_path <- ta_render_pdf()
      ta_export_status(paste0("PDF saved: ", basename(pdf_path),
                               "\n", format(Sys.time(), "%Y-%m-%d %H:%M:%S")))
      showNotification(paste0("PDF saved: ", basename(pdf_path)), type = "message")
    }, error = function(e) {
      msg <- paste0("PDF error: ", conditionMessage(e))
      ta_export_status(msg)
      showNotification(msg, type = "error")
    })
  })

  observeEvent(input$btn_ta_email, {
    email_to <- trimws(input$ta_email)
    if (email_to == "") {
      showNotification("Enter an email address first.", type = "warning")
      return()
    }
    ta_export_status("Generating PDF\u2026")
    tryCatch({
      pdf_path <- ta_render_pdf()
      ta_export_status("PDF ready — sending email\u2026")
      sc        <- tryCatch(
        fromJSON("/home/ufuser/Fpren-main/weather_rss/config/smtp_config.json"),
        error = function(e) list())
      smtp_host <- sc$smtp_host %||% "smtp.ufl.edu"
      smtp_port <- as.integer(sc$smtp_port %||% 25)
      mail_from <- sc$mail_from %||% email_to
      library(emayili)
      em <- envelope() %>%
        from(mail_from) %>%
        to(email_to) %>%
        subject(paste0("FPREN Traffic Analysis Report — ", format(Sys.Date(), "%Y-%m-%d"))) %>%
        text(paste0(
          "FPREN Traffic Analysis Report\n\n",
          "Filters:   ", ta_filter_label(), "\n",
          "Incidents: ", nrow(ta_filtered()), "\n",
          "Generated: ", format(Sys.time(), "%Y-%m-%d %H:%M:%S"), "\n\n",
          "Please find the PDF report attached.\n\n",
          "-- FPREN Automated Reporting System\n",
          "   Florida Public Radio Emergency Network\n"
        )) %>%
        attachment(pdf_path)
      server(host = smtp_host, port = smtp_port, reuse = FALSE)(em, verbose = FALSE)
      msg <- paste0("Email sent to ", email_to, " at ", format(Sys.time(), "%H:%M:%S"))
      ta_export_status(msg)
      showNotification(msg, type = "message")
    }, error = function(e) {
      msg <- paste0("Error: ", conditionMessage(e))
      ta_export_status(msg)
      showNotification(msg, type = "error")
    })
  })

  # ── County Alerts tab ────────────────────────────────────────────────────────

  ca_selected_county  <- reactiveVal("Alachua")
  ca_error_msg        <- reactiveVal("")
  ca_report_status_rv <- reactiveVal("")

  # ZIP input → auto-select county in dropdown
  observeEvent(input$ca_zip, {
    z <- trimws(input$ca_zip)
    if (grepl("^\\d{5}$", z)) {
      county <- zip_to_florida_county(z)
      if (!is.na(county))
        updateSelectInput(session, "ca_county", selected = county)
    }
  }, ignoreInit = TRUE)

  # County dropdown → show a ZIP hint
  output$ca_zip_hint <- renderUI({
    county <- input$ca_county
    if (is.null(county) || county == "-- Select a county --") return(NULL)
    # Show first known ZIP in the ranges for this county
    idx <- which(FL_ZIP_RANGES$county == county)
    hint_zip <- if (length(idx) > 0)
      sprintf("%05d", FL_ZIP_RANGES$from[idx[1]]) else "n/a"
    tags$small(style = "color:#888",
      icon("info-circle"), sprintf(" %s ZIP codes begin around: %s", county, hint_zip))
  })

  # Search button
  observeEvent(input$btn_ca_search, {
    ca_error_msg("")
    z      <- trimws(input$ca_zip)
    county <- input$ca_county

    if (nchar(z) > 0) {
      if (!grepl("^\\d{5}$", z)) {
        ca_error_msg("Invalid ZIP code: must be exactly 5 digits.")
        return()
      }
      resolved <- zip_to_florida_county(z)
      if (is.na(resolved)) {
        ca_error_msg(paste0(
          "ZIP code ", z,
          " is not a valid Florida ZIP code. Florida ZIPs range from 32004 to 34997."))
        return()
      }
      ca_selected_county(resolved)
    } else if (!is.null(county) && county != "-- Select a county --") {
      ca_selected_county(county)
    } else {
      ca_error_msg("Please enter a Florida ZIP code or select a county.")
    }
  })

  # Error display
  output$ca_error_ui <- renderUI({
    msg <- ca_error_msg()
    if (nchar(msg) == 0) return(NULL)
    div(
      style = paste(
        "background-color:#c0392b; color:white; padding:10px 15px;",
        "border-radius:4px; margin-bottom:12px; font-weight:bold;"
      ),
      icon("exclamation-circle"), " ", msg
    )
  })

  # MongoDB query for county alerts (auto-refreshes every 60 s)
  county_alerts_data <- reactive({
    county <- ca_selected_county()
    if (is.null(county)) return(data.frame())
    invalidateLater(60000)
    slug  <- tolower(gsub("[. ]", "_", gsub("\\.", "", county)))
    query <- sprintf(
      '{"$or":[{"area_desc":{"$regex":"%s","$options":"i"}},{"source":"county_nws:%s"}]}',
      county, slug
    )
    col <- get_col("nws_alerts")
    if (is.null(col)) return(data.frame())
    tryCatch({
      df <- col$find(query,
        fields = '{"alert_id":1,"event":1,"headline":1,"severity":1,
                   "area_desc":1,"sent":1,"expires":1,"source":1,
                   "description":1,"fetched_at":1,"_id":0}')
      col$disconnect()
      df
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL)
      data.frame()
    })
  })

  # Zone audio files for this county
  county_wavs_data <- reactive({
    county <- ca_selected_county()
    if (is.null(county)) return(data.frame())
    invalidateLater(60000)
    zone  <- COUNTY_TO_ZONE[county]
    if (is.na(zone)) zone <- "all_florida"
    col   <- get_col("zone_alert_wavs")
    if (is.null(col)) return(data.frame())
    q <- sprintf('{"zone":{"$in":["%s","all_florida"]}}', zone)
    tryCatch({
      df <- col$find(q,
        fields = '{"zone":1,"event":1,"generated_at":1,"_id":0}',
        sort   = '{"generated_at":-1}', limit = 20)
      col$disconnect()
      df
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL)
      data.frame()
    })
  })

  # Summary value boxes
  output$ca_box_name <- renderValueBox({
    county <- ca_selected_county()
    valueBox(if (is.null(county)) "\u2014" else county,
             "Selected County", icon = icon("map-marker-alt"), color = "blue")
  })

  output$ca_box_total <- renderValueBox({
    df <- county_alerts_data()
    n  <- nrow(df)
    valueBox(n, "Active Alerts", icon = icon("exclamation-triangle"),
             color = if (n > 0) "red" else "green")
  })

  output$ca_box_updated <- renderValueBox({
    county_alerts_data()   # take dependency so box updates on refresh
    valueBox(format(Sys.time(), "%H:%M:%S"), "Last Updated",
             icon = icon("clock"), color = "blue")
  })

  # Alerts DataTable with severity color coding
  output$tbl_ca_alerts <- renderDT({
    df <- county_alerts_data()
    if (is.null(ca_selected_county())) {
      return(datatable(data.frame(
        Message = "Enter a Florida ZIP code or select a county and click Search Alerts"
      ), options = list(dom = "t"), rownames = FALSE))
    }
    if (nrow(df) == 0) {
      ca_error_msg(paste0("No active alerts found for ", ca_selected_county(), "."))
      return(datatable(data.frame(
        Message = paste0("No active alerts found for ", ca_selected_county())
      ), options = list(dom = "t"), rownames = FALSE))
    }
    ca_error_msg("")
    display <- df %>%
      select(any_of(c("event","severity","headline","area_desc","sent","expires","source"))) %>%
      mutate(across(everything(), as.character))
    datatable(
      display,
      selection = "single",
      rownames  = FALSE,
      options   = list(pageLength = 10, scrollX = TRUE)
    ) %>%
      formatStyle("severity",
        backgroundColor = styleEqual(
          c("Extreme","Severe","Moderate","Minor"),
          c("#c0392b","#e67e22","#f39c12","#ecf0f1")),
        color = styleEqual(
          c("Extreme","Severe","Moderate","Minor"),
          c("white","white","black","black")))
  })

  # Full description of selected alert row
  output$ca_alert_description <- renderText({
    df  <- county_alerts_data()
    sel <- input$tbl_ca_alerts_rows_selected
    if (is.null(sel) || length(sel) == 0 || nrow(df) == 0)
      return("Click an alert row above to view its full description.")
    if (!"description" %in% names(df))
      return("No description field available.")
    desc <- df$description[sel]
    if (is.na(desc) || nchar(trimws(desc)) == 0)
      return("No description available for this alert.")
    desc
  })

  # PDF generation
  observeEvent(input$btn_ca_pdf, {
    county <- ca_selected_county()
    if (is.null(county)) {
      showNotification("Search for a county first.", type = "warning")
      return()
    }
    ca_report_status_rv("Generating PDF report\u2026 (this may take 30\u201360 s)")
    tryCatch({
      output_dir  <- "/home/ufuser/Fpren-main/reports/output"
      dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)
      timestamp   <- format(Sys.time(), "%Y%m%d_%H%M")
      safe_county <- gsub("[^A-Za-z0-9]", "_", county)
      output_file <- file.path(output_dir,
        paste0("county_alerts_", safe_county, "_", timestamp, ".pdf"))
      withr::with_dir(tempdir(), rmarkdown::render(
        input             = "/home/ufuser/Fpren-main/reports/county_alerts_report.Rmd",
        output_file       = output_file,
        intermediates_dir = tempdir(),
        params            = list(county_name = county,
                                 date        = format(Sys.Date(), "%Y-%m-%d"),
                                 mongo_uri   = MONGO_URI),
        quiet = TRUE
      ))
      ca_report_status_rv(paste0(
        "Report saved: ", basename(output_file), "\n",
        format(Sys.time(), "%Y-%m-%d %H:%M:%S")))
      showNotification(paste0("PDF saved: ", basename(output_file)), type = "message")
    }, error = function(e) {
      msg <- paste0("PDF error: ", conditionMessage(e))
      ca_report_status_rv(msg)
      showNotification(msg, type = "error")
    })
  })

  # Email report
  observeEvent(input$btn_ca_email, {
    county <- ca_selected_county()
    if (is.null(county)) {
      showNotification("Search for a county first.", type = "warning")
      return()
    }
    output_dir  <- "/home/ufuser/Fpren-main/reports/output"
    safe_county <- gsub("[^A-Za-z0-9]", "_", county)
    files <- list.files(output_dir,
      pattern   = paste0("county_alerts_", safe_county, "_.*\\.pdf$"),
      full.names = TRUE)
    if (length(files) == 0) {
      showNotification("No PDF found \u2014 generate it first.", type = "warning")
      return()
    }
    latest_file <- files[which.max(file.mtime(files))]
    ca_report_status_rv("Sending email\u2026")
    tryCatch({
      sc        <- tryCatch(
        fromJSON("/home/ufuser/Fpren-main/weather_rss/config/smtp_config.json"),
        error = function(e) list())
      smtp_host <- sc$smtp_host %||% "smtp.ufl.edu"
      smtp_port <- as.integer(sc$smtp_port %||% 25)
      mail_from <- sc$mail_from %||% "lawrence.bornace@ufl.edu"
      mail_to   <- sc$mail_to   %||% "lawrence.bornace@ufl.edu"
      subject   <- sprintf("FPREN County Alert Report - %s - %s",
                            county, format(Sys.Date(), "%Y-%m-%d"))
      library(emayili)
      em <- envelope() %>%
        from(mail_from) %>%
        to(mail_to) %>%
        subject(subject) %>%
        text(paste0(
          "FPREN County Alert Report\n\n",
          "County:    ", county, "\n",
          "Date:      ", format(Sys.Date(), "%Y-%m-%d"), "\n",
          "Generated: ", format(Sys.time(), "%Y-%m-%d %H:%M:%S UTC"), "\n\n",
          "Please find the PDF report attached.\n\n",
          "-- FPREN Automated Reporting System\n",
          "   Florida Public Radio Emergency Network\n"
        )) %>%
        attachment(latest_file)
      server(host = smtp_host, port = smtp_port, reuse = FALSE)(em, verbose = FALSE)
      msg <- paste0("Email sent to ", mail_to, " at ", format(Sys.time(), "%H:%M:%S"))
      ca_report_status_rv(msg)
      showNotification(msg, type = "message")
    }, error = function(e) {
      msg <- paste0("Email error: ", conditionMessage(e))
      ca_report_status_rv(msg)
      showNotification(msg, type = "error")
    })
  })

  output$ca_report_status <- renderText({ ca_report_status_rv() })

  # ── Airport tab ─────────────────────────────────────────────────────────────

  output$box_airports_delayed <- renderValueBox({
    df <- airport_data()
    n  <- if (nrow(df) == 0) 0 else sum(df$has_delay == TRUE, na.rm = TRUE)
    valueBox(n, "Airports Delayed", icon = icon("exclamation-circle"),
             color = if (n > 0) "red" else "green")
  })

  output$box_airports_ok <- renderValueBox({
    df <- airport_data()
    n  <- if (nrow(df) == 0) 0 else sum(df$has_delay == FALSE, na.rm = TRUE)
    valueBox(n, "Airports Normal", icon = icon("check-circle"), color = "green")
  })

  output$box_airports_total <- renderValueBox({
    n <- max(nrow(airport_data()), nrow(metar_data()))
    valueBox(n, "Airports Monitored", icon = icon("globe"), color = "blue")
  })

  output$tbl_airports <- renderDT({
    delays <- airport_data()
    metars <- metar_data()

    if (nrow(metars) == 0 && nrow(delays) == 0)
      return(datatable(data.frame(Message = "No airport data available")))

    if (nrow(metars) > 0) {
      df <- metars
      if (nrow(delays) > 0 && "icao" %in% names(delays)) {
        df <- df %>% left_join(
          delays %>% select(any_of(c("icao","state","has_delay"))),
          by = "icao"
        )
      } else {
        df$has_delay <- NA
      }
    } else {
      df <- delays %>%
        mutate(temp_f = "\u2014", wind = "\u2014", visibility = "\u2014",
               sky = "\u2014", obs_time = "\u2014")
    }

    df <- df %>%
      mutate(
        delay_status = case_when(
          is.na(has_delay) ~ "Unknown",
          has_delay         ~ "DELAYED",
          TRUE              ~ "Normal"
        )
      ) %>%
      select(any_of(c("icao","name","state","delay_status",
                       "temp_f","wind","visibility","sky","obs_time"))) %>%
      arrange(desc(delay_status))

    names(df)[names(df) == "icao"]         <- "ICAO"
    names(df)[names(df) == "name"]         <- "Airport"
    names(df)[names(df) == "state"]        <- "State"
    names(df)[names(df) == "delay_status"] <- "Delay Status"
    names(df)[names(df) == "temp_f"]       <- "Temp"
    names(df)[names(df) == "wind"]         <- "Wind"
    names(df)[names(df) == "visibility"]   <- "Visibility"
    names(df)[names(df) == "sky"]          <- "Sky/Cat"
    names(df)[names(df) == "obs_time"]     <- "Obs Time"

    datatable(df, options = list(pageLength = 20, scrollX = TRUE),
              rownames = FALSE) %>%
      formatStyle("Delay Status",
        color      = styleEqual(c("DELAYED","Normal","Unknown"),
                                c("red","green","gray")),
        fontWeight = styleEqual(c("DELAYED","Normal","Unknown"),
                                c("bold","normal","normal"))) %>%
      formatStyle("Sky/Cat",
        color = styleEqual(c("IFR","LIFR","MVFR","VFR"),
                           c("#cc0000","#cc0000","#ff8800","#006600")))
  })

  # ── Station health tab ──────────────────────────────────────────────────────

  output$tbl_wavs <- renderDT({
    df <- wav_data()
    if (nrow(df) == 0) return(datatable(data.frame(Message = "No audio files found")))
    df <- df %>% select(any_of(c("source_type","zone","event","tts_engine","generated_at")))
    datatable(df, options = list(pageLength = 15, scrollX = TRUE), rownames = FALSE)
  })

  output$txt_r_version  <- renderText({ R.version.string })
  output$txt_last_refresh <- renderText({ format(Sys.time(), "%Y-%m-%d %H:%M:%S UTC") })

  output$txt_collections <- renderText({
    cols <- c("nws_alerts","airport_delays","zone_alert_wavs","feed_status")
    results <- sapply(cols, function(c) {
      col <- get_col(c)
      if (is.null(col)) paste0(c, ": ERROR")
      else {
        n <- tryCatch({ col$count(); }, error = function(e) "?")
        col$disconnect()
        paste0(c, ": ", n, " docs")
      }
    })
    paste(results, collapse = "\n")
  })

  # ── Feed status tab ─────────────────────────────────────────────────────────

  output$tbl_feeds <- renderDT({
    df <- feed_data()
    if (nrow(df) == 0) return(datatable(data.frame(Message = "No feed status data")))
    datatable(df, options = list(pageLength = 20, scrollX = TRUE),
              rownames = FALSE) %>%
      formatStyle("status",
        color      = styleEqual(c("OK","ERROR"), c("green","red")),
        fontWeight = styleEqual("ERROR", "bold"))
  })

  # ── Icecast tab ─────────────────────────────────────────────────────────────

  icecast_timer <- reactiveTimer(60000)  # auto-refresh every 60 s

  icecast_data <- reactive({
    input$btn_ice_refresh
    icecast_timer()
    tryCatch({
      raw  <- readLines("http://localhost:8000/status-json.xsl", warn = FALSE)
      json <- fromJSON(paste(raw, collapse = ""))
      json$icestats
    }, error = function(e) NULL)
  })

  # Normalise source list — Icecast returns a list when 1 mount, data.frame when >1
  icecast_sources <- reactive({
    ic <- icecast_data()
    if (is.null(ic) || is.null(ic$source)) return(data.frame())
    src <- ic$source
    if (is.data.frame(src)) src
    else as.data.frame(do.call(rbind, lapply(src, as.data.frame)), stringsAsFactors = FALSE)
  })

  output$box_ice_total_listeners <- renderValueBox({
    src <- icecast_sources()
    total <- if (nrow(src) == 0) 0 else sum(as.integer(src$listeners), na.rm = TRUE)
    valueBox(total, "Total Listeners", icon = icon("headphones"), color = "blue")
  })

  output$box_ice_active_mounts <- renderValueBox({
    src <- icecast_sources()
    valueBox(nrow(src), "Active Mounts", icon = icon("broadcast-tower"), color = "green")
  })

  output$box_ice_peak_listeners <- renderValueBox({
    src <- icecast_sources()
    peak <- if (nrow(src) == 0) 0 else max(as.integer(src$listener_peak), na.rm = TRUE)
    valueBox(peak, "Peak Listeners", icon = icon("chart-line"), color = "purple")
  })

  output$box_ice_server_uptime <- renderValueBox({
    ic <- icecast_data()
    uptime <- if (!is.null(ic) && !is.null(ic$server_start)) {
      start <- tryCatch(as.POSIXct(ic$server_start_iso8601, format = "%Y-%m-%dT%H:%M:%S",
                                   tz = "America/New_York"), error = function(e) NULL)
      if (!is.null(start)) {
        secs <- as.numeric(difftime(Sys.time(), start, units = "secs"))
        hrs  <- floor(secs / 3600)
        mins <- floor((secs %% 3600) / 60)
        paste0(hrs, "h ", mins, "m")
      } else "—"
    } else "—"
    valueBox(uptime, "Server Uptime", icon = icon("clock"), color = "yellow")
  })

  output$tbl_icecast_mounts <- renderDT({
    src <- icecast_sources()
    if (nrow(src) == 0)
      return(datatable(data.frame(Message = "Icecast not reachable or no active mounts")))
    # Extract mount path from listenurl
    mount <- sub("http://[^/]+", "", src$listenurl)
    df <- data.frame(
      Mount        = mount,
      Name         = sub("FPREN Florida Public Radio Emergency Network ?—? ?", "", src$server_name),
      Listeners    = as.integer(src$listeners),
      `Peak`       = as.integer(src$listener_peak),
      `Stream Start` = format(as.POSIXct(src$stream_start_iso8601, format = "%Y-%m-%dT%H:%M:%S",
                                          tz = "America/New_York"), "%m/%d %H:%M"),
      Type         = src$server_type,
      stringsAsFactors = FALSE, check.names = FALSE
    )
    df <- df[order(df$Mount), ]
    datatable(df, rownames = FALSE,
              options = list(pageLength = 15, scrollX = TRUE, dom = "t")) %>%
      formatStyle("Listeners",
        backgroundColor = styleInterval(c(0), c("transparent", "#dff0d8")))
  })

  output$tbl_ice_server_info <- renderTable({
    ic <- icecast_data()
    if (is.null(ic)) return(data.frame(Field = "Status", Value = "Icecast unreachable"))
    data.frame(
      Field = c("Server", "Host", "Location", "Admin", "Started"),
      Value = c(
        ic$server_id   %||% "—",
        ic$host        %||% "—",
        ic$location    %||% "—",
        ic$admin       %||% "—",
        ic$server_start %||% "—"
      ),
      stringsAsFactors = FALSE
    )
  }, striped = TRUE, hover = TRUE, bordered = FALSE, spacing = "s")

  output$ui_ice_stream_urls <- renderUI({
    src <- icecast_sources()
    if (nrow(src) == 0) return(p("No active mounts."))
    mount_paths <- sort(sub("http://[^/]+", "", src$listenurl))
    base        <- "http://128.227.67.234:8000"
    tags$ul(
      lapply(mount_paths, function(m) {
        url <- paste0(base, m)
        tags$li(tags$a(href = url, target = "_blank", url))
      })
    )
  })

  # ── Stream Alerts tab ───────────────────────────────────────────────────────

  stream_cfg_rv  <- reactiveVal(read_notify_config())
  stream_status_msg <- reactiveVal("")

  # Populate UI fields from config on load
  observe({
    cfg <- stream_cfg_rv()
    updateCheckboxGroupInput(session, "stream_notify_methods",
      selected = if (is.null(cfg$notify_methods)) "email" else cfg$notify_methods)
    updateTextInput(session, "stream_email",        value = cfg$email %||% "")
    updateTextInput(session, "stream_phone",        value = cfg$phone %||% "")
    updateTextInput(session, "stream_twilio_sid",   value = cfg$twilio_sid %||% "")
    updateTextInput(session, "stream_twilio_token", value = cfg$twilio_token %||% "")
    updateTextInput(session, "stream_twilio_from",  value = cfg$twilio_from %||% "")
    updateCheckboxInput(session, "stream_notify_offline", value = isTRUE(cfg$notify_on_offline))
    updateCheckboxInput(session, "stream_notify_reboot",  value = isTRUE(cfg$notify_on_reboot))
  })

  output$box_stream_status <- renderValueBox({
    if (input$auto_refresh) timer()
    is_up <- check_stream_port()
    valueBox(
      if (is_up) "ONLINE" else "OFFLINE",
      "Icecast Stream (port 8000)",
      icon  = icon(if (is_up) "broadcast-tower" else "exclamation-circle"),
      color = if (is_up) "green" else "red"
    )
  })

  output$box_stream_last_check <- renderValueBox({
    if (input$auto_refresh) timer()
    valueBox(format(Sys.time(), "%H:%M:%S"), "Last Checked",
             icon = icon("clock"), color = "blue")
  })

  output$box_stream_state_file <- renderValueBox({
    if (input$auto_refresh) timer()
    state <- read_stream_state()
    valueBox(toupper(state), "Recorded State",
             icon = icon("database"), color = "purple")
  })

  observeEvent(input$btn_save_stream_cfg, {
    cfg <- list(
      notify_methods    = input$stream_notify_methods,
      email             = trimws(input$stream_email),
      phone             = trimws(input$stream_phone),
      twilio_sid        = trimws(input$stream_twilio_sid),
      twilio_token      = trimws(input$stream_twilio_token),
      twilio_from       = trimws(input$stream_twilio_from),
      notify_on_offline = isTRUE(input$stream_notify_offline),
      notify_on_reboot  = isTRUE(input$stream_notify_reboot)
    )
    save_notify_config(cfg)
    stream_cfg_rv(cfg)
    stream_status_msg(paste0("Settings saved at ", format(Sys.time(), "%H:%M:%S")))
  })

  observeEvent(input$btn_test_notify, {
    stream_status_msg("Sending test notification...")
    result <- tryCatch({
      system2("/usr/bin/python3",
              args = c("/home/ufuser/Fpren-main/scripts/stream_notify.py", "offline"),
              stdout = TRUE, stderr = TRUE, wait = TRUE)
      "Test notification sent — check your email/SMS/phone."
    }, error = function(e) paste0("Error: ", conditionMessage(e)))
    stream_status_msg(result)
  })

  output$stream_cfg_status <- renderText({ stream_status_msg() })

  output$tbl_notify_log <- renderDT({
    input$btn_refresh_notify_log
    if (input$auto_refresh) timer()
    datatable(read_notify_log(), options = list(pageLength = 10, dom = "tp"),
              rownames = FALSE, colnames = "Notification Log")
  })

  # ── Config tab ──────────────────────────────────────────────────────────────

  SMTP_CONFIG_PATH <- "/home/ufuser/Fpren-main/weather_rss/config/smtp_config.json"

  read_smtp_config <- function() {
    if (file.exists(SMTP_CONFIG_PATH))
      tryCatch(fromJSON(SMTP_CONFIG_PATH), error = function(e) list())
    else list()
  }

  cfg_smtp_status_msg <- reactiveVal("")

  # Populate SMTP fields on load
  observe({
    sc <- read_smtp_config()
    if (length(sc) == 0) return()
    updateTextInput(session,    "cfg_smtp_host",  value = sc$smtp_host  %||% "")
    updateNumericInput(session, "cfg_smtp_port",  value = as.integer(sc$smtp_port %||% 25))
    updateTextInput(session,    "cfg_smtp_user",  value = sc$smtp_user  %||% "")
    updateTextInput(session,    "cfg_smtp_pass",  value = sc$smtp_pass  %||% "")
    updateTextInput(session,    "cfg_mail_from",  value = sc$mail_from  %||% "")
    updateTextInput(session,    "cfg_mail_to",    value = sc$mail_to    %||% "")
    updateCheckboxInput(session,"cfg_use_tls",    value = isTRUE(sc$use_tls))
    updateCheckboxInput(session,"cfg_use_auth",   value = isTRUE(sc$use_auth))
  })

  observeEvent(input$btn_save_smtp, {
    sc <- list(
      smtp_host = trimws(input$cfg_smtp_host),
      smtp_port = input$cfg_smtp_port,
      smtp_user = trimws(input$cfg_smtp_user),
      smtp_pass = trimws(input$cfg_smtp_pass),
      mail_from = trimws(input$cfg_mail_from),
      mail_to   = trimws(input$cfg_mail_to),
      use_tls   = isTRUE(input$cfg_use_tls),
      use_auth  = isTRUE(input$cfg_use_auth)
    )
    tryCatch({
      dir.create(dirname(SMTP_CONFIG_PATH), recursive = TRUE, showWarnings = FALSE)
      write(toJSON(sc, auto_unbox = TRUE, pretty = TRUE), SMTP_CONFIG_PATH)
      cfg_smtp_status_msg(paste0("SMTP settings saved at ", format(Sys.time(), "%H:%M:%S")))
    }, error = function(e) cfg_smtp_status_msg(paste0("Save error: ", conditionMessage(e))))
  })

  observeEvent(input$btn_test_smtp, {
    cfg_smtp_status_msg("Sending test email...")
    sc <- read_smtp_config()
    mail_to   <- trimws(sc$mail_to   %||% "")
    mail_from <- trimws(sc$mail_from %||% "")
    smtp_host <- trimws(sc$smtp_host %||% "")
    smtp_port <- as.integer(sc$smtp_port %||% 25)
    if (mail_to == "" || smtp_host == "") {
      cfg_smtp_status_msg("Error: save SMTP settings (host + mail_to) before sending a test.")
      return()
    }
    result <- tryCatch({
      em <- emayili::envelope(
        to      = mail_to,
        from    = if (mail_from != "") mail_from else mail_to,
        subject = "FPREN SMTP Test",
        text    = paste0("SMTP test from FPREN dashboard at ", format(Sys.time(), "%Y-%m-%d %H:%M:%S"))
      )
      srv <- if (isTRUE(sc$use_tls)) {
        emayili::server(host = smtp_host, port = smtp_port,
                        username = sc$smtp_user %||% "", password = sc$smtp_pass %||% "",
                        reuse = FALSE)
      } else {
        emayili::server(host = smtp_host, port = smtp_port, reuse = FALSE)
      }
      srv(em, verbose = FALSE)
      paste0("Test email sent to ", mail_to, " at ", format(Sys.time(), "%H:%M:%S"))
    }, error = function(e) paste0("Error: ", conditionMessage(e)))
    cfg_smtp_status_msg(result)
  })

  output$cfg_smtp_status <- renderText({ cfg_smtp_status_msg() })

  svc_status_badge <- function(up) {
    if (up)
      tags$span(class = "label label-success", "UP")
    else
      tags$span(class = "label label-danger", "DOWN")
  }

  check_svc_status <- reactive({
    input$btn_cfg_refresh_status
    if (input$auto_refresh) timer()
    list(
      icecast = check_stream_port("127.0.0.1", 8000),
      shiny   = check_stream_port("127.0.0.1", 3838),
      flask   = check_stream_port("127.0.0.1", 5000),
      monitor = tryCatch({
        length(system2("systemctl", args = c("is-active", "--quiet", "stream-monitor"),
                       stdout = TRUE, stderr = TRUE, wait = TRUE)) == 0
      }, error = function(e) FALSE)
    )
  })

  output$cfg_svc_icecast <- renderUI({ svc_status_badge(check_svc_status()$icecast) })
  output$cfg_svc_shiny   <- renderUI({ svc_status_badge(check_svc_status()$shiny)   })
  output$cfg_svc_flask   <- renderUI({ svc_status_badge(check_svc_status()$flask)   })
  output$cfg_svc_monitor <- renderUI({
    up <- tryCatch({
      system2("systemctl", args = c("is-active", "--quiet", "stream-monitor"),
              stdout = FALSE, stderr = FALSE, wait = TRUE) == 0
    }, error = function(e) FALSE)
    svc_status_badge(up)
  })

  # ── Auto-refresh ────────────────────────────────────────────────────────────
  observe({
    if (input$auto_refresh) timer()
    output$txt_last_refresh <- renderText({
      format(Sys.time(), "%Y-%m-%d %H:%M:%S UTC")
    })
  })

  # ── Reports tab ─────────────────────────────────────────────────────────────
  rpt_status_msg <- reactiveVal("")
  rpt_output_dir <- "/home/ufuser/Fpren-main/reports/output"

  output$rpt_status <- renderText({ rpt_status_msg() })

  output$tbl_reports <- renderDT({
    input$btn_gen_report  # re-render after generation
    files <- list.files(rpt_output_dir, pattern = "\\.pdf$",
                        full.names = FALSE)
    if (length(files) == 0)
      return(datatable(data.frame(Message = "No reports generated yet")))
    df <- data.frame(
      File     = sort(files, decreasing = TRUE),
      stringsAsFactors = FALSE
    )
    datatable(df, options = list(pageLength = 10), rownames = FALSE,
              selection = "none")
  })

  observeEvent(input$btn_gen_report, {
    rpt_status_msg("Generating report — this may take 30–60 seconds...")
    days  <- as.integer(input$rpt_days)
    zone  <- input$rpt_zone
    email <- input$rpt_email

    withCallingHandlers(
      tryCatch({
        output_dir  <- rpt_output_dir
        dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)
        timestamp   <- format(Sys.time(), "%Y%m%d_%H%M")
        output_file <- file.path(output_dir,
                                  paste0("fpren_alert_report_", timestamp, ".pdf"))
        withr::with_dir(tempdir(), rmarkdown::render(
          input             = "/home/ufuser/Fpren-main/reports/fpren_alert_report.Rmd",
          output_file       = output_file,
          intermediates_dir = tempdir(),
          params            = list(days_back  = days,
                                   zone_label = zone,
                                   mongo_uri  = MONGO_URI),
          quiet = TRUE
        ))
        msg <- paste0("Report saved: ", basename(output_file))
        if (email) {
          ret <- system2(
            "/usr/bin/Rscript",
            args = c("/home/ufuser/Fpren-main/reports/generate_and_email.R",
                     as.character(days), shQuote(zone)),
            stdout = TRUE, stderr = TRUE
          )
          if (any(grepl("Email sent", ret)))
            msg <- paste0(msg, "\nEmail sent to lawrence.bornace@ufl.edu")
          else
            msg <- paste0(msg, "\nEmail failed — check logs.")
        }
        rpt_status_msg(msg)
      }, error = function(e) {
        rpt_status_msg(paste0("ERROR: ", conditionMessage(e)))
      })
    )
  })

  # ── Enhanced User Management (Admin Only) ────────────────────────────────────
  user_mgmt_msg  <- reactiveVal("")
  user_mgmt_rv   <- reactiveVal(0)

  output$users_table <- DT::renderDataTable({
    user_mgmt_rv()
    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) return(data.frame(Message="DB unavailable"))
    tryCatch({
      u <- col$find("{}", fields = '{"password":0,"verify_code":0,"reset_code":0,"invite_token":0,"_id":0}')
      col$disconnect()
      if (nrow(u) == 0) return(data.frame(Message="No users found"))
      # Select display columns
      keep <- intersect(c("username","email","phone","role","active",
                          "email_verified","phone_verified","last_login",
                          "created_at","created_by"), names(u))
      u[, keep, drop=FALSE]
    }, error = function(e) {
      tryCatch(col$disconnect(), error=function(e2) NULL)
      data.frame(Error=conditionMessage(e))
    })
  }, selection = "single", options = list(pageLength=10), rownames = FALSE)

  observeEvent(input$btn_add_user, {
    if (!isTRUE(auth_rv$role == "admin")) {
      user_mgmt_msg("Admin role required."); return()
    }
    email <- trimws(input$new_user_email)
    phone <- trimws(input$new_user_phone)
    role  <- input$new_user_role
    if (nchar(email) == 0) { user_mgmt_msg("Email is required."); return() }

    # Derive username from email prefix
    uname <- tolower(gsub("[^a-z0-9._]", "", strsplit(email, "@")[[1]][1]))
    if (nchar(uname) == 0) uname <- paste0("user", format(Sys.time(), "%Y%m%d%H%M%S"))

    temp_pw    <- paste0(sample(c(letters, LETTERS, 0:9), 8, replace=TRUE), collapse="")
    pw_hash    <- bcrypt::hashpw(temp_pw)
    invite_tok <- gen_token()
    now_str    <- format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz="UTC")

    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) { user_mgmt_msg("DB unavailable."); return() }
    tryCatch({
      col$insert(list(
        username             = uname,
        email                = email,
        phone                = phone,
        password             = pw_hash,
        role                 = role,
        active               = TRUE,
        email_verified       = FALSE,
        phone_verified       = FALSE,
        must_change_password = TRUE,
        failed_attempts      = 0L,
        locked_until         = NULL,
        last_login           = NULL,
        created_at           = now_str,
        created_by           = auth_rv$username %||% "admin",
        invite_token         = invite_tok,
        verify_code          = NULL,
        verify_expires       = NULL,
        reset_code           = NULL,
        reset_expires        = NULL
      ))
      col$disconnect()
    }, error=function(e) {
      tryCatch(col$disconnect(), error=function(e2) NULL)
      user_mgmt_msg(paste("Error creating user:", conditionMessage(e)))
      return()
    })

    # Send invite email
    send_fpren_email(email,
      "You have been invited to the FPREN Dashboard",
      paste0(
        "<h2>Welcome to FPREN Dashboard</h2>",
        "<p>An account has been created for you on the Florida Public Radio Emergency Network dashboard.</p>",
        "<table style='border-collapse:collapse;margin:16px 0;'>",
        "<tr><td style='padding:4px 12px 4px 0;font-weight:bold;'>Username:</td><td>", uname, "</td></tr>",
        "<tr><td style='padding:4px 12px 4px 0;font-weight:bold;'>Temporary Password:</td><td>", temp_pw, "</td></tr>",
        "<tr><td style='padding:4px 12px 4px 0;font-weight:bold;'>Role:</td><td>", role, "</td></tr>",
        "</table>",
        "<p><strong>You will be required to change your password on first login.</strong></p>",
        "<p>Dashboard URL: <a href='https://128.227.67.234'>https://128.227.67.234</a></p>",
        "<p>For help, contact <a href='mailto:lawrence.bornace@ufl.edu'>lawrence.bornace@ufl.edu</a></p>"
      ))

    # Log and notify
    log_audit("user_add", uname, auth_rv$username %||% "admin",
              paste("Added user", uname, "(", email, ") with role", role))

    send_notification_emails(
      paste("FPREN: New user added:", uname),
      paste0("<h3>FPREN User Management Notification</h3>",
             "<p><strong>Action:</strong> New user added</p>",
             "<p><strong>Username:</strong> ", uname, "</p>",
             "<p><strong>Email:</strong> ", email, "</p>",
             "<p><strong>Role:</strong> ", role, "</p>",
             "<p><strong>Performed by:</strong> ", auth_rv$username %||% "admin", "</p>",
             "<p><strong>Date/Time:</strong> ", now_str, "</p>")
    )

    user_mgmt_msg(paste("User", uname, "created and invite sent to", email))
    updateTextInput(session, "new_user_email", value="")
    updateTextInput(session, "new_user_phone", value="")
    user_mgmt_rv(user_mgmt_rv() + 1)
  })

  observeEvent(input$btn_delete_user, {
    if (!isTRUE(auth_rv$role == "admin")) {
      user_mgmt_msg("Admin role required."); return()
    }
    sel <- input$users_table_rows_selected
    if (is.null(sel) || length(sel) == 0) {
      user_mgmt_msg("Select a user row first, then click Delete.")
      return()
    }
    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) { user_mgmt_msg("DB unavailable."); return() }
    tryCatch({
      u <- col$find("{}", fields = '{"username":1,"email":1,"_id":0}')
      col$disconnect()
      target_user  <- u$username[sel]
      target_email <- if (!is.null(u$email)) u$email[sel] else ""
      showModal(modalDialog(
        title = "Confirm Delete",
        tags$p("Are you sure you want to delete user ",
               tags$strong(target_user), "?"),
        tags$p(tags$small("This cannot be undone.")),
        tags$input(type="hidden", id="delete_target_user", value=target_user),
        footer = tagList(
          modalButton("Cancel"),
          actionButton("btn_confirm_delete", "Delete",
                       class = "btn-danger", icon = icon("trash"))
        )
      ))
    }, error = function(e) {
      tryCatch(col$disconnect(), error=function(e2) NULL)
      user_mgmt_msg(paste("Error:", conditionMessage(e)))
    })
  })

  observeEvent(input$btn_confirm_delete, {
    removeModal()
    if (!isTRUE(auth_rv$role == "admin")) return()
    sel <- input$users_table_rows_selected
    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) { user_mgmt_msg("DB unavailable."); return() }
    tryCatch({
      u <- col$find("{}", fields = '{"username":1,"email":1,"_id":0}')
      target_user  <- u$username[sel]
      target_email <- if (!is.null(u$email)) as.character(u$email[sel]) else ""
      col$remove(sprintf('{"username":"%s"}', target_user))
      col$disconnect()
      now_str <- format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz="UTC")
      log_audit("user_delete", target_user, auth_rv$username %||% "admin",
                paste("Deleted user", target_user))
      send_notification_emails(
        paste("FPREN: User deleted:", target_user),
        paste0("<h3>FPREN User Management Notification</h3>",
               "<p><strong>Action:</strong> User deleted</p>",
               "<p><strong>Username:</strong> ", target_user, "</p>",
               "<p><strong>Email:</strong> ", target_email, "</p>",
               "<p><strong>Performed by:</strong> ", auth_rv$username %||% "admin", "</p>",
               "<p><strong>Date/Time:</strong> ", now_str, "</p>")
      )
      user_mgmt_msg(paste("User", target_user, "deleted."))
      user_mgmt_rv(user_mgmt_rv() + 1)
    }, error = function(e) {
      tryCatch(col$disconnect(), error=function(e2) NULL)
      user_mgmt_msg(paste("Error:", conditionMessage(e)))
    })
  })

  output$user_mgmt_status <- renderText({ user_mgmt_msg() })

  # Upload Content
  CONTENT_ROOT <- "/home/ufuser/Fpren-main/weather_station/audio/content"
  upload_msg <- reactiveVal("")
  output$upload_file_list <- DT::renderDataTable({
    input$btn_upload; input$upload_folder
    folder <- file.path(CONTENT_ROOT, input$upload_folder)
    if (!dir.exists(folder)) return(data.frame(Message="Folder not found"))
    files <- list.files(folder, pattern="\\.(mp3|wav|ogg|m4a)$", ignore.case=TRUE)
    if (length(files)==0) return(data.frame(Message="No files yet"))
    data.frame(Filename=files, Size_KB=file.size(file.path(folder,files))%/%1024,
               stringsAsFactors=FALSE)
  }, options=list(pageLength=20), rownames=FALSE)
  observeEvent(input$btn_upload, {
    req(input$upload_file)
    folder <- file.path(CONTENT_ROOT, input$upload_folder)
    dir.create(folder, showWarnings=FALSE, recursive=TRUE)
    results <- sapply(seq_len(nrow(input$upload_file)), function(i) {
      tryCatch({ file.copy(input$upload_file$datapath[i],
        file.path(folder, input$upload_file$name[i]), overwrite=TRUE)
        paste("OK:", input$upload_file$name[i])
      }, error=function(e) paste("FAIL:", input$upload_file$name[i]))
    })
    upload_msg(paste(results, collapse="\n"))
  })
  output$upload_status <- renderText({ upload_msg() })

  # ── Weather Trends Report ────────────────────────────────────────────────────
  wt_status_msg <- reactiveVal("")
  output$wt_status <- renderText({ wt_status_msg() })

  observeEvent(input$btn_gen_wx_trend, {
    wt_status_msg("Generating weather trends report\u2026 (30\u201360 s)")
    icao      <- input$wt_city
    city_name <- WX_CITIES$city[WX_CITIES$icao == icao]
    if (length(city_name) == 0) city_name <- icao
    start_d   <- as.character(input$wt_dates[1])
    end_d     <- as.character(input$wt_dates[2])
    email     <- isTRUE(input$wt_email)
    tryCatch({
      output_dir  <- "/home/ufuser/Fpren-main/reports/output"
      dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)
      timestamp   <- format(Sys.time(), "%Y%m%d_%H%M")
      safe_city   <- gsub("[^A-Za-z0-9]", "_", city_name)
      output_file <- file.path(output_dir,
        paste0("weather_trends_", safe_city, "_", timestamp, ".pdf"))
      withr::with_dir(tempdir(), rmarkdown::render(
        input             = "/home/ufuser/Fpren-main/reports/weather_trends_report.Rmd",
        output_file       = output_file,
        intermediates_dir = tempdir(),
        params            = list(icao       = icao,
                                 city_name  = city_name,
                                 start_date = start_d,
                                 end_date   = end_d,
                                 mongo_uri  = MONGO_URI),
        quiet = TRUE
      ))
      msg <- paste0("Report saved: ", basename(output_file))
      if (email) {
        sc        <- tryCatch(fromJSON("/home/ufuser/Fpren-main/weather_rss/config/smtp_config.json"),
                              error = function(e) list())
        smtp_host <- sc$smtp_host %||% "smtp.ufl.edu"
        smtp_port <- as.integer(sc$smtp_port %||% 25)
        mail_from <- sc$mail_from %||% "lawrence.bornace@ufl.edu"
        mail_to   <- sc$mail_to   %||% "lawrence.bornace@ufl.edu"
        library(emayili)
        em <- envelope() %>%
          from(mail_from) %>% to(mail_to) %>%
          subject(paste0("FPREN Weather Trends — ", city_name,
                         " (", start_d, " to ", end_d, ")")) %>%
          text(paste0("Weather Trends Report\nCity: ", city_name,
                      "\nPeriod: ", start_d, " to ", end_d,
                      "\nGenerated: ", format(Sys.time(), "%Y-%m-%d %H:%M UTC"))) %>%
          attachment(output_file)
        server(host = smtp_host, port = smtp_port, reuse = FALSE)(em, verbose = FALSE)
        msg <- paste0(msg, "\nEmail sent to ", mail_to)
      }
      wt_status_msg(msg)
    }, error = function(e) {
      wt_status_msg(paste0("ERROR: ", conditionMessage(e)))
    })
  })

  # ── Zones / Playlist Config ───────────────────────────────────────────────

  output$zone_pl_info <- renderUI({
    zone_id <- input$zone_pl_sel
    col <- get_col("zone_definitions")
    if (is.null(col)) return(NULL)
    z <- tryCatch({
      res <- col$find(sprintf('{"zone_id":"%s"}', zone_id))
      col$disconnect()
      res
    }, error = function(e) { tryCatch(col$disconnect(), error=function(e2) NULL); data.frame() })
    if (nrow(z) == 0) return(NULL)
    row      <- z[1, ]
    counties <- if (!is.null(row$counties[[1]])) paste(row$counties[[1]], collapse=", ") else "—"
    tagList(
      hr(),
      p(strong("Display name:"), row$display_name),
      p(strong("Counties:"), counties),
      p(strong("Catch-all:"), if (isTRUE(row$catch_all)) "Yes" else "No")
    )
  })

  observeEvent(input$zone_pl_sel, {
    zone_id <- input$zone_pl_sel
    col <- get_col("zone_definitions")
    if (is.null(col)) return()
    z <- tryCatch({
      res <- col$find(sprintf('{"zone_id":"%s"}', zone_id),
                      fields='{"normal_mode_types":1,"_id":0}')
      col$disconnect()
      res
    }, error=function(e) { tryCatch(col$disconnect(), error=function(e2) NULL); data.frame() })
    defaults <- c("fire","flooding","freeze","fog","other_alerts",
                  "weather_report","traffic","airport_weather",
                  "educational","imaging","top_of_hour")
    selected <- if (nrow(z) > 0 && !is.null(z$normal_mode_types) &&
                    length(z$normal_mode_types[[1]]) > 0)
                  z$normal_mode_types[[1]] else defaults
    updateCheckboxGroupInput(session, "normal_playlist_types", selected=selected)
  }, ignoreInit=FALSE)

  pl_save_status <- reactiveVal("")
  output$playlist_save_status <- renderText({ pl_save_status() })

  observeEvent(input$btn_save_playlist_config, {
    zone_id <- input$zone_pl_sel
    types   <- input$normal_playlist_types %||% character(0)
    col <- get_col("zone_definitions")
    if (is.null(col)) { pl_save_status("Error: MongoDB unavailable."); return() }
    tryCatch({
      types_json <- paste0('["', paste(types, collapse='","'), '"]')
      col$update(sprintf('{"zone_id":"%s"}', zone_id),
                 sprintf('{"$set":{"normal_mode_types":%s}}', types_json))
      col$disconnect()
      pl_save_status(sprintf("Saved %d types for %s at %s",
                             length(types), zone_id,
                             format(Sys.time(), "%H:%M:%S")))
    }, error=function(e) {
      tryCatch(col$disconnect(), error=function(e2) NULL)
      pl_save_status(paste0("Save error: ", conditionMessage(e)))
    })
  })

  output$zone_audio_inventory <- DT::renderDataTable({
    zone_id    <- input$zone_pl_sel
    zones_root <- "/home/ufuser/Fpren-main/weather_station/audio/zones"
    zone_dir   <- file.path(zones_root, zone_id)
    all_types  <- c("priority_1","tornado","thunderstorm","hurricane",
                    "fire","flooding","freeze","fog","other_alerts",
                    "weather_report","traffic","airport_weather",
                    "educational","imaging","top_of_hour")
    labels     <- c("Priority 1","Tornado","Severe Thunderstorm","Hurricane / Tropical",
                    "Fire","Flooding","Freeze / Winter","Fog","Other Alerts",
                    "Weather Reports","Traffic","Airport Weather",
                    "Educational","Imaging","Top of Hour")
    modes      <- c(rep("P1 Interrupt", 4), rep("Normal", 11))
    rows <- mapply(function(ct, lbl, mode) {
      folder <- file.path(zone_dir, ct)
      files  <- if (dir.exists(folder))
                  list.files(folder, pattern="\\.(mp3|wav|ogg)$", full.names=TRUE)
                else character(0)
      newest <- if (length(files) > 0)
                  format(as.POSIXct(max(file.mtime(files))), "%Y-%m-%d %H:%M")
                else "—"
      data.frame(Category=lbl, Mode=mode, Files=length(files), Newest=newest,
                 stringsAsFactors=FALSE)
    }, all_types, labels, modes, SIMPLIFY=FALSE)
    df <- do.call(rbind, rows)
    DT::datatable(df, options=list(pageLength=20, dom="t"), rownames=FALSE) %>%
      DT::formatStyle("Mode",
        backgroundColor=DT::styleEqual(c("P1 Interrupt","Normal"),
                                        c("#fadbd8","#d5f5e3")))
  }, server=FALSE)

  output$zones_table <- DT::renderDataTable({
    col <- get_col("zone_definitions")
    if (is.null(col)) return(data.frame(Message="MongoDB unavailable"))
    tryCatch({
      z <- col$find("{}", fields='{"zone_id":1,"display_name":1,"catch_all":1,"counties":1,"_id":0}')
      col$disconnect()
      if (nrow(z) == 0) return(data.frame(Message="No zones found"))
      z$counties <- sapply(z$counties, function(x) paste(unlist(x), collapse=", "))
      z
    }, error=function(e) {
      tryCatch(col$disconnect(), error=function(e2) NULL)
      data.frame(Error=conditionMessage(e))
    })
  }, options=list(pageLength=15), rownames=FALSE)

}


shinyApp(ui, server)
