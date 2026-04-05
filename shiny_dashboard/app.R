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

send_fpren_email <- function(to, subject, body_html, attachment_path = NULL) {
  # Validate recipient
  if (is.null(to) || nchar(trimws(to)) == 0) {
    message("Email skipped: no recipient specified"); return(FALSE)
  }
  sc <- tryCatch(fromJSON("/home/ufuser/Fpren-main/weather_rss/config/smtp_config.json"),
                 error = function(e) list())
  smtp_host <- sc$smtp_host %||% "smtp.ufl.edu"
  smtp_port <- as.integer(if (!is.null(sc$smtp_port) && nchar(as.character(sc$smtp_port)) > 0) sc$smtp_port else 25)
  mail_from <- sc$mail_from %||% "lawrence.bornace@ufl.edu"
  full_html <- paste0(
    '<!DOCTYPE html><html><head><meta charset="UTF-8">',
    '<meta name="viewport" content="width=device-width, initial-scale=1.0"></head>',
    '<body style="font-family:Arial,sans-serif;max-width:680px;margin:0 auto;padding:16px;color:#333;">',
    body_html, UF_BANNER_HTML, '</body></html>'
  )
  # Delegate to Python helper — avoids emayili/Shiny namespace conflicts
  tryCatch({
    tmp_html <- tempfile(fileext = ".html")
    tmp_cfg  <- tempfile(fileext = ".json")
    writeLines(full_html, tmp_html)
    cfg_list <- list(
      to         = to,
      subject    = subject,
      mail_from  = mail_from,
      smtp_host  = smtp_host,
      smtp_port  = smtp_port,
      use_tls    = isTRUE(sc$use_tls),
      use_auth   = isTRUE(sc$use_auth),
      smtp_user  = sc$smtp_user %||% "",
      smtp_pass  = sc$smtp_pass %||% "",
      attachment = if (!is.null(attachment_path) && file.exists(attachment_path)) attachment_path else ""
    )
    write_json(cfg_list, tmp_cfg, auto_unbox = TRUE)
    py_helper <- "/home/ufuser/Fpren-main/shiny_dashboard/send_email.py"
    result <- system2("python3", args = c(py_helper, tmp_cfg, tmp_html),
                      stdout = TRUE, stderr = TRUE, timeout = 30)
    try(file.remove(tmp_html), silent = TRUE)
    try(file.remove(tmp_cfg),  silent = TRUE)
    if (any(grepl("^OK", result))) {
      TRUE
    } else {
      err_msg <- paste(result, collapse = " ")
      message("[FPREN Email] Failed to ", to, ": ", substr(err_msg, 1, 200))
      FALSE
    }
  }, error = function(e) {
    message("[FPREN Email] Error sending to ", to, ": ", e$message)
    FALSE
  })
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
MONGO_URI     <- Sys.getenv("MONGO_URI", "mongodb://localhost:27017/")
DASHBOARD_URL <- Sys.getenv("FPREN_DASHBOARD_URL", "https://128.227.67.234")

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
  style = paste0("position:fixed;top:0;left:0;width:100%;height:100%;",
                 "background:#003087;z-index:9999;",
                 "display:flex;align-items:center;justify-content:center;overflow-y:auto;"),

  # ── JS: Enter key on invite form ─────────────────────────────────────────────
  tags$script(HTML("
    document.addEventListener('DOMContentLoaded', function() {
      ['login_password','login_username'].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener('keydown', function(e) {
          if (e.key === 'Enter') { document.getElementById('btn_login').click(); }
        });
      });
      ['invite_pw1','invite_pw2'].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener('keydown', function(e) {
          if (e.key === 'Enter') { document.getElementById('btn_accept_invite').click(); }
        });
      });
    });
  ")),

  div(style = "max-width:520px;width:100%;margin:auto;padding:16px;",

    # FPREN header
    div(style = "text-align:center;margin-bottom:20px;",
      tags$div(style = "color:white;padding:20px 0 12px;",
        tags$h1(style = "margin:0;font-size:36px;font-weight:800;letter-spacing:2px;", "FPREN"),
        tags$p(style = "margin:4px 0 0;font-size:15px;opacity:0.9;",
               "Florida Public Radio Emergency Network"),
        tags$p(style = "margin:2px 0 0;font-size:12px;opacity:0.7;", "University of Florida")
      )
    ),

    # ── Panel A: Invite-Only message (shown by default) ───────────────────────
    div(id = "panel_invite_only",
      div(style = paste0("background:white;border-radius:8px;",
                         "box-shadow:0 4px 24px rgba(0,0,0,0.25);padding:32px;"),

        div(style = "text-align:center;padding-bottom:16px;",
          tags$span(style = "font-size:52px;color:#003087;", HTML("&#128274;")),
          tags$h4(style = "margin:12px 0 6px;color:#003087;",
                  "Access by Invitation Only"),
          tags$p(style = "color:#555;font-size:14px;",
            "The FPREN Dashboard is restricted to authorized personnel.",
            tags$br(),
            "If you have received an invitation, use the link in your email or SMS.",
            tags$br(),
            "To request access, contact ",
            tags$a(href = "mailto:lawrence.bornace@ufl.edu",
                   "lawrence.bornace@ufl.edu"), "."
          )
        ),

        # Invite error/status (shown when token is bad/expired)
        uiOutput("invite_landing_msg"),

        tags$hr(style = "margin:12px 0;"),

        # Collapsible staff/admin login
        tags$details(
          tags$summary(
            style = "cursor:pointer;color:#888;font-size:12px;text-align:center;user-select:none;",
            "FPREN Staff / Admin Login"
          ),
          div(style = "padding-top:14px;",
            textInput("login_username", "Username", placeholder = "Username"),
            passwordInput("login_password", "Password", placeholder = "Password"),
            uiOutput("login_attempts_msg"),
            br(),
            actionButton("btn_login", "Login",
                         class = "btn-primary btn-block",
                         style = "font-size:15px;padding:9px;"),
            br(),
            actionButton("btn_forgot", "Forgot username or password?",
                         class = "btn-link",
                         style = "width:100%;text-align:center;font-size:12px;")
          )
        ),

        tags$hr(style = "margin:14px 0 10px;"),
        div(style = "background:#fff8e1;border:1px solid #ffe082;border-radius:4px;padding:10px 12px;font-size:11px;color:#555;",
          tags$strong("NOTICE \u2014 UF Acceptable Use Policy"),
          tags$p(style = "margin-top:6px;margin-bottom:0;", AUP_TEXT)
        )
      )
    ),

    # ── Panel B: Invite Acceptance (hidden; shown after valid ?invite=TOKEN) ───
    div(id = "panel_invite_accept", style = "display:none;",
      div(style = paste0("background:white;border-radius:8px;",
                         "box-shadow:0 4px 24px rgba(0,0,0,0.25);padding:32px;"),
        div(style = "text-align:center;margin-bottom:16px;",
          tags$span(style = "font-size:40px;", HTML("&#127381;")),
          tags$h4(style = "color:#003087;margin:10px 0 4px;",
                  "Activate Your FPREN Account"),
          uiOutput("invite_accept_msg")
        ),
        passwordInput("invite_pw1", "Set Password (8+ characters)",
                      placeholder = "New password"),
        passwordInput("invite_pw2", "Confirm Password",
                      placeholder = "Repeat password"),
        br(),
        actionButton("btn_accept_invite", "Activate Account & Sign In",
                     class = "btn-success btn-block",
                     style = "font-size:15px;padding:9px;"),
        br(),
        div(style = "background:#fff3cd;border:1px solid #ffc107;border-radius:4px;padding:8px 12px;font-size:11px;",
          icon("info-circle"),
          " After setting your password you will be guided through SMS and email verification."
        ),
        tags$hr(style = "margin:14px 0 10px;"),
        div(style = "background:#fff8e1;border:1px solid #ffe082;border-radius:4px;padding:10px 12px;font-size:11px;color:#555;",
          tags$strong("NOTICE \u2014 UF Acceptable Use Policy"),
          tags$p(style = "margin-top:6px;margin-bottom:0;", AUP_TEXT)
        )
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
      style = "line-height: 1.2; white-space: normal;",
      tags$div(style = "font-size: 15px; font-weight: bold;", "FPREN"),
      tags$div(style = "font-size: 11px; font-weight: normal; opacity: 0.9;",
               "Florida Public Radio Emergency Network")
    ),
    titleWidth = 280
  ),

  dashboardSidebar(width = 280,
    sidebarMenuOutput("sidebar_menu")
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
            if (idleMinutes >= 4 && !warnShown) {
              warnShown = true;
              if (window.Shiny) Shiny.setInputValue('idle_warn', Math.random());
            }
            if (idleMinutes >= 5) {
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
        ),
        # ── FPREN System / SNMP Status ────────────────────────────────────────
        fluidRow(
          valueBoxOutput("snmp_box_health",   width = 3),
          valueBoxOutput("snmp_box_services", width = 3),
          valueBoxOutput("snmp_box_alerts",   width = 3),
          valueBoxOutput("snmp_box_wx_cat",   width = 3)
        ),
        fluidRow(
          box(title = tagList(icon("server"), " FPREN Service OID Status"),
              width = 7, status = "info", solidHeader = TRUE,
              p(tags$small(
                "Live service status from the FPREN SNMP agent. Polling community: ",
                code("fpren_monitor"), " / OID base: ",
                code("1.3.6.1.4.1.64533"), " — updated every 60 s.")),
              actionButton("btn_snmp_refresh", "Refresh Now",
                           class = "btn-xs btn-default", icon = icon("sync")),
              br(), br(),
              DTOutput("tbl_snmp_services")),
          box(title = tagList(icon("map-marker-alt"), " User Asset OID Map"),
              width = 5, status = "primary", solidHeader = TRUE,
              p(tags$small(
                "Each registered user asset is addressable via SNMP OID.",
                " Poll individual assets from your SNMP management station.")),
              DTOutput("tbl_snmp_asset_oids"))
        ),
        fluidRow(
          box(
            title = tagList(icon("exclamation-triangle"), " Offline / Unreachable SNMP Devices"),
            width = 12, status = "danger", solidHeader = TRUE, collapsible = TRUE,
            p(tags$small(
              icon("info-circle"),
              " SNMP devices registered to user assets that are offline, unreachable, or not yet checked.",
              " Devices are ", tags$strong("never auto-polled"),
              " — click ", tags$strong("Recheck"), " to test TCP connectivity once and store the result."
            )),
            actionButton("btn_snmp_offline_refresh", "Refresh List",
                         class = "btn-sm btn-warning", icon = icon("sync")),
            br(), br(),
            uiOutput("snmp_offline_devices_ui")
          )
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
                column(2, style = "padding-left:24px;", br(),
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
        # ── Flight Category Legend ───────────────────────────────────────────
        fluidRow(
          column(12,
            div(style = "display:flex; align-items:center; gap:18px; padding:8px 14px 6px 14px; flex-wrap:wrap;",
              tags$span(style = "font-size:12px; color:#888; font-weight:600; margin-right:4px;",
                        icon("info-circle"), " Flight Category:"),
              tags$span(style = "display:inline-flex; align-items:center; gap:6px;",
                div(style = "width:14px; height:14px; border-radius:3px; background:#1a6bb5; display:inline-block;"),
                tags$span(style = "font-size:12px; color:#333;",
                  tags$strong("VFR"), " — Visual Flight Rules (good visibility)")),
              tags$span(style = "display:inline-flex; align-items:center; gap:6px;",
                div(style = "width:14px; height:14px; border-radius:3px; background:#b5860a; display:inline-block;"),
                tags$span(style = "font-size:12px; color:#333;",
                  tags$strong("MVFR"), " — Marginal VFR (reduced visibility)")),
              tags$span(style = "display:inline-flex; align-items:center; gap:6px;",
                div(style = "width:14px; height:14px; border-radius:3px; background:#c0460a; display:inline-block;"),
                tags$span(style = "font-size:12px; color:#333;",
                  tags$strong("IFR"), " — Instrument Flight Rules (low visibility)")),
              tags$span(style = "display:inline-flex; align-items:center; gap:6px;",
                div(style = "width:14px; height:14px; border-radius:3px; background:#8b0000; display:inline-block;"),
                tags$span(style = "font-size:12px; color:#333;",
                  tags$strong("LIFR"), " — Low IFR (very poor conditions)")),
              tags$span(style = "display:inline-flex; align-items:center; gap:6px;",
                div(style = "width:14px; height:14px; border-radius:3px; background:#5a5a5a; display:inline-block;"),
                tags$span(style = "font-size:12px; color:#333;",
                  tags$strong("UNK"), " — Data unavailable"))
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
              p(tags$small(style="color:#666;",
                  icon("info-circle"),
                  " Circle size = incident count per county. Color = worst severity. Click a county to filter the table above.")),
              leafletOutput("traffic_map", height = "480px"))
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
                column(4, style = "padding-left: 28px;",
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
                column(4,
                  selectInput("ca_pdf_county", "County for Report",
                    choices = c("All Florida",
                                sort(c("Alachua","Baker","Bay","Bradford","Brevard","Broward",
                                  "Calhoun","Charlotte","Citrus","Clay","Collier","Columbia",
                                  "DeSoto","Dixie","Duval","Escambia","Flagler","Franklin",
                                  "Gadsden","Gilchrist","Glades","Gulf","Hamilton","Hardee",
                                  "Hendry","Hernando","Highlands","Hillsborough","Holmes",
                                  "Indian River","Jackson","Jefferson","Lafayette","Lake",
                                  "Lee","Leon","Levy","Liberty","Madison","Manatee","Marion",
                                  "Martin","Miami-Dade","Monroe","Nassau","Okaloosa","Okeechobee",
                                  "Orange","Osceola","Palm Beach","Pasco","Pinellas","Polk",
                                  "Putnam","St. Johns","St. Lucie","Santa Rosa","Sarasota",
                                  "Seminole","Sumter","Suwannee","Taylor","Union","Volusia",
                                  "Wakulla","Walton","Washington"))),
                    selected = "All Florida")
                ),
                column(3, br(),
                  actionButton("btn_ca_pdf", "Generate PDF Report",
                               class = "btn-primary", icon = icon("file-pdf"))
                ),
                column(3, br(),
                  actionButton("btn_ca_email", "Email Report",
                               class = "btn-default", icon = icon("envelope"))
                ),
                column(2,
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
        ),

        # ── Inovonics 677 EAS LP-1 Monitor ────────────────────────────────────
        fluidRow(
          box(title = tagList(icon("broadcast-tower"),
                              " Inovonics 677 EAS LP-1 Monitor — 10.245.74.39"),
              width = 12, status = "warning", solidHeader = TRUE,
              div(style = "background:#fff8e1;border-left:4px solid #f39c12;padding:10px 14px;margin-bottom:14px;border-radius:0 4px 4px 0;font-size:13px;color:#555;",
                tags$strong(icon("exclamation-circle"), " Compatibility Notice:"),
                tags$span(" This monitoring interface is designed and configured exclusively for the ",
                  tags$strong("Inovonics Model 677 EAS LP-1 Monitoring Receiver"),
                  ". The SNMP OIDs, data fields, and status indicators displayed here are specific to the ",
                  tags$strong("Inovonics EN677"),
                  " and its firmware implementation. Use with any other device, manufacturer, or model is not supported and will produce incorrect or no data. ",
                  "SNMP community string and OID assignments are drawn directly from the ",
                  tags$strong("Inovonics BARON MIB"),
                  " for this product line."
                )
              ),
              fluidRow(
                column(6,
                  uiOutput("eas_device_status_ui")
                ),
                column(6, style = "text-align:right; padding-top:6px;",
                  tags$small(style="color:#888;", "Last poll: "),
                  uiOutput("eas_last_poll_ui", inline = TRUE),
                  tags$span(" "),
                  actionButton("btn_eas_poll", "Poll Now",
                               class = "btn-sm btn-warning", icon = icon("sync"))
                )
              ),
              hr(),
              uiOutput("eas_sources_ui"),
              br(),
              # Raw OID walk — collapsed by default, admin only
              conditionalPanel(condition = "output.is_admin",
                tags$details(
                  tags$summary(style = "cursor:pointer; color:#888; font-size:12px;",
                               icon("code"), " Raw SNMP OID Walk (Admin)"),
                  br(),
                  DT::dataTableOutput("tbl_eas_raw_oids")
                )
              )
          )
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

      # ── Census & Demographics ────────────────────────────────────────────────
      tabItem(tabName = "census",
        fluidRow(
          valueBoxOutput("census_vbox_pop",        width = 3),
          valueBoxOutput("census_vbox_elderly",     width = 3),
          valueBoxOutput("census_vbox_poverty",     width = 3),
          valueBoxOutput("census_vbox_vulnerable",  width = 3)
        ),
        fluidRow(
          box(title = "County Selector", width = 4, status = "primary", solidHeader = TRUE,
              selectInput("census_county_sel", "Florida County",
                choices = c("Loading..." = ""), selected = ""),
              br(),
              actionButton("btn_census_analyze", "AI Vulnerability Analysis",
                           class = "btn-primary", icon = icon("brain")),
              br(), br(),
              verbatimTextOutput("census_ai_output"),
              hr(),
              p(tags$small(icon("info-circle"),
                " Data: US Census ACS 5-Year Estimates. ",
                tags$a("Census API", href="https://api.census.gov", target="_blank"),
                " | AI: UF LiteLLM (llama-3.3-70b)"))
          ),
          box(title = "County Demographics", width = 8, status = "info", solidHeader = TRUE,
              uiOutput("census_county_detail"),
              hr(),
              plotOutput("census_vulnerability_chart", height = "220px")
          )
        ),
        fluidRow(
          box(title = "Active Alert — Population Impact", width = 12,
              status = "warning", solidHeader = TRUE,
              p(tags$small("Select an active NWS alert to see AI-generated population impact assessment using Census data.")),
              selectInput("census_alert_sel", "Active Alert",
                choices = c("Loading..." = ""), selected = ""),
              fluidRow(
                column(2,
                  actionButton("btn_census_impact", "Analyze Impact",
                               class = "btn-warning", icon = icon("exclamation-triangle"))
                ),
                column(2,
                  actionButton("btn_census_impact_pdf", "Export PDF",
                               class = "btn-primary", icon = icon("file-pdf"))
                ),
                column(2,
                  actionButton("btn_census_impact_email", "Email Report",
                               class = "btn-default", icon = icon("envelope"))
                ),
                column(6,
                  verbatimTextOutput("census_impact_status")
                )
              ),
              br(),
              uiOutput("census_impact_output")
          )
        ),
        fluidRow(
          box(title = "All 67 FL Counties — Vulnerability Rankings", width = 12,
              status = "primary", solidHeader = TRUE,
              p(tags$small("Sorted by vulnerability score (elderly %, poverty %, limited English %, disability %). ",
                strong("Admin only:"), " use Refresh button to pull fresh data from the Census API.")),
              fluidRow(
                column(8, DT::dataTableOutput("census_all_table")),
                column(4,
                  conditionalPanel(
                    condition = "output.is_admin",
                    br(),
                    actionButton("btn_census_refresh", "Refresh from Census API",
                                 class = "btn-danger btn-sm", icon = icon("cloud-download-alt")),
                    br(), br(),
                    verbatimTextOutput("census_refresh_status"),
                    hr(),
                    p(tags$small("Census API key is stored in:"),
                      br(),
                      code("weather_rss/config/census_config.json"),
                      br(),
                      tags$small("or env var CENSUS_API_KEY"))
                  )
                )
              )
          )
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
                p(tags$small("Click a user row to select them. A profile card will appear below with options to edit or delete.")),
                DT::dataTableOutput("users_table"),
                br(),
                uiOutput("user_profile_card"),
                hr(),
                h5(icon("comment-dots"), " SMS & Role Management"),
                p(tags$small("Click a cell to edit role or SMS opt-in. Click ",
                  tags$strong("Save SMS / Role Changes"), " to persist.")),
                DTOutput("user_sms_table"),
                br(),
                fluidRow(
                  column(6,
                    actionButton("btn_save_sms_roles", "Save SMS / Role Changes",
                                 class = "btn-warning", icon = icon("save"))),
                  column(6, verbatimTextOutput("sms_roles_status"))
                ),
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
                fluidRow(
                  column(6,
                    selectInput("new_user_profession", "Profession",
                      choices = c(
                        list("-- Select a profession --" = ""),
                        list(
                          "Broadcast" = c(
                            "Broadcast Engineer"      = "Broadcast Engineer",
                            "Broadcast Administrator" = "Broadcast Administrator",
                            "Broadcast Operator"      = "Broadcast Operator",
                            "Program Director"        = "Program Director",
                            "Chief Engineer"          = "Chief Engineer",
                            "News Director"           = "News Director",
                            "Production Manager"      = "Production Manager"
                          ),
                          "Law Enforcement" = c(
                            "Police Chief"                   = "Police Chief",
                            "Police Lieutenant"              = "Police Lieutenant",
                            "Police Officer"                 = "Police Officer",
                            "Campus Security Officer"        = "Campus Security Officer",
                            "Dispatch Coordinator"           = "Dispatch Coordinator",
                            "Emergency Services Coordinator" = "Emergency Services Coordinator"
                          ),
                          "Emergency Management" = c(
                            "County Emergency Manager" = "County Emergency Manager",
                            "City Administrator"       = "City Administrator",
                            "Public Safety Director"   = "Public Safety Director",
                            "EOC Coordinator"          = "EOC Coordinator",
                            "FEMA Liaison"             = "FEMA Liaison",
                            "Hazmat Coordinator"       = "Hazmat Coordinator"
                          ),
                          "General" = c(
                            "Facility Manager"         = "Facility Manager",
                            "IT/Systems Administrator" = "IT/Systems Administrator",
                            "Station Manager"          = "Station Manager",
                            "Other"                    = "Other"
                          )
                        )
                      ),
                      selected = "")
                  ),
                  column(6, br(),
                    uiOutput("profession_bcp_hint")
                  )
                ),
                actionButton("btn_add_user", "Add User & Send Invite",
                             class = "btn-success", icon = icon("user-plus")),
                verbatimTextOutput("user_mgmt_status")
            )
          )
        ),
        conditionalPanel(
          condition = "output.is_admin",
          fluidRow(
            box(title = "User Assets / Properties (Admin Only)", width = 12, status = "primary",
                solidHeader = TRUE,
                p(tags$small(icon("info-circle"),
                  " Select a user above to view and manage their assets.",
                  " Assets store the physical location of each property with LAT/LON for BCP generation.")),
                fluidRow(
                  column(4, selectInput("asset_mgmt_user", "Selected User",
                    choices = c("-- select a user --" = ""), selected = "")),
                  column(4, br(),
                    actionButton("btn_load_user_assets", "Load Assets",
                                 class = "btn-primary btn-sm", icon = icon("sync")))
                ),
                DT::dataTableOutput("user_assets_table"),
                br(),
                fluidRow(
                  column(3,
                    actionButton("btn_asset_move_up", icon("arrow-up"), " Move Up",
                                 class = "btn-default btn-sm")
                  ),
                  column(3,
                    actionButton("btn_asset_move_down", icon("arrow-down"), " Move Down",
                                 class = "btn-default btn-sm")
                  ),
                  column(3,
                    actionButton("btn_delete_asset", "Remove Selected",
                                 class = "btn-warning btn-sm", icon = icon("trash"))
                  )
                ),
                uiOutput("asset_nearby_panel"),
                uiOutput("asset_snmp_devices_panel"),
                fluidRow(
                  column(12,
                    div(style="margin-top:6px;",
                      actionButton("btn_refresh_nearby", "Refresh Nearby Resources",
                                   icon = icon("sync"), class = "btn-xs btn-default"),
                      verbatimTextOutput("nearby_refresh_status")
                    )
                  )
                ),
                hr(),
                h5(icon("plus"), " Add New Asset"),
                fluidRow(
                  column(4, textInput("new_asset_name",    "Asset Name",    placeholder = "WUFT Studio B")),
                  column(4, textInput("new_asset_address", "Full Address",   placeholder = "1600 SW 23rd Dr, Gainesville, FL 32608")),
                  column(4, selectInput("new_asset_type", "Asset Type",
                    choices = c("Radio Station","Transmitter Site","Office","Tower","Data Center",
                                "Remote Studio","Repeater Site","Facility","Other")))
                ),
                fluidRow(
                  column(3, textInput("new_asset_zip",   "ZIP Code",  placeholder = "32608")),
                  column(3, uiOutput("new_asset_city_ui")),
                  column(3, numericInput("new_asset_lat", "Latitude",  value = NULL, step = 0.0001)),
                  column(3, numericInput("new_asset_lon", "Longitude", value = NULL, step = -0.0001))
                ),
                fluidRow(
                  column(6, uiOutput("new_asset_airport_ui")),
                  column(6, textInput("new_asset_notes", "Notes (optional)", placeholder = ""))
                ),
                br(),
                actionButton("btn_lookup_zip",  "Lookup City & Airport from ZIP",
                             class = "btn-info btn-sm", icon = icon("search")),
                tags$span(style = "margin-left: 16px;"),
                actionButton("btn_add_asset", "Add Asset",
                             class = "btn-success", icon = icon("plus")),
                br(), br(),
                verbatimTextOutput("asset_mgmt_status")
            )
          )
        ),

        # ── Emergency SMS ────────────────────────────────────────────────────
        conditionalPanel(
          condition = "output.is_admin",
          fluidRow(
            box(title = tagList(icon("comment-dots"), " Emergency SMS Notifications (Admin Only)"),
                width = 12, status = "danger", solidHeader = TRUE,

                # ── To-Do List Editor ──────────────────────────────────────
                h5(icon("list"), " Role-Based Action Checklists"),
                p(tags$small(
                  "Define per-phase to-do lists for each profession.",
                  " These are delivered as numbered SMS bullet lists during emergencies.")),
                fluidRow(
                  column(3,
                    selectInput("todo_role", "Profession",
                      choices = c(
                        "Broadcast Engineer","Broadcast Administrator","Broadcast Operator",
                        "Program Director","Chief Engineer","News Director","Production Manager",
                        "Police Chief","Police Lieutenant","Police Officer",
                        "Campus Security Officer","Dispatch Coordinator",
                        "Emergency Services Coordinator","County Emergency Manager",
                        "City Administrator","Public Safety Director","EOC Coordinator",
                        "FEMA Liaison","Hazmat Coordinator","Facility Manager",
                        "IT/Systems Administrator","Station Manager","Other"
                      ),
                      selected = "Broadcast Engineer")
                  ),
                  column(9,
                    fluidRow(
                      column(4,
                        tags$label("Before Event"),
                        textAreaInput("todo_before", NULL, rows = 7,
                          placeholder = "One action per line:\nVerify generator fuel\nTest backup comms...")
                      ),
                      column(4,
                        tags$label("During Event"),
                        textAreaInput("todo_during", NULL, rows = 7,
                          placeholder = "One action per line:\nMonitor all streams\nAlert management team...")
                      ),
                      column(4,
                        tags$label("After Event"),
                        textAreaInput("todo_after", NULL, rows = 7,
                          placeholder = "One action per line:\nVerify all systems restored\nFile incident report...")
                      )
                    )
                  )
                ),
                fluidRow(
                  column(3,
                    actionButton("btn_load_todos", "Load for Role",
                                 class = "btn-info btn-sm", icon = icon("download"))),
                  column(3,
                    actionButton("btn_save_todos", "Save for Role",
                                 class = "btn-success btn-sm", icon = icon("save"))),
                  column(6, verbatimTextOutput("todo_edit_status"))
                ),

                hr(),

                # ── SMS Blast ─────────────────────────────────────────────
                h5(icon("paper-plane"), " Send Emergency SMS Blast"),
                p(tags$small(
                  "Sends role-specific action items as a numbered SMS to all opted-in users",
                  " (or a filtered role group). Requires Twilio credentials in Stream Alerts tab.")),
                fluidRow(
                  column(3,
                    selectInput("sms_blast_role", "Target Role (blank = all)",
                      choices = c(
                        "All SMS-Enabled Users" = "__all__",
                        "Broadcast Engineer","Broadcast Administrator","Broadcast Operator",
                        "Program Director","Chief Engineer","News Director","Production Manager",
                        "Police Chief","Police Lieutenant","Police Officer",
                        "Campus Security Officer","Dispatch Coordinator",
                        "Emergency Services Coordinator","County Emergency Manager",
                        "City Administrator","Public Safety Director","EOC Coordinator",
                        "FEMA Liaison","Hazmat Coordinator","Facility Manager",
                        "IT/Systems Administrator","Station Manager","Other"
                      ),
                      selected = "__all__")
                  ),
                  column(3,
                    selectInput("sms_blast_phase", "Phase",
                      choices = c("Before Event" = "before",
                                  "During Event" = "during",
                                  "After Event"  = "after"),
                      selected = "before")
                  ),
                  column(3, br(),
                    actionButton("btn_preview_sms", "Preview SMS",
                                 class = "btn-info", icon = icon("eye"))
                  ),
                  column(3, br(),
                    actionButton("btn_send_sms_blast", "Send SMS Now",
                                 class = "btn-danger", icon = icon("paper-plane"))
                  )
                ),
                verbatimTextOutput("sms_blast_preview"),
                verbatimTextOutput("sms_blast_status")
            )
          )
        ),

        # ── Test SMS ─────────────────────────────────────────────────────────
        conditionalPanel(
          condition = "output.is_admin",
          fluidRow(
            box(title = tagList(icon("mobile-alt"), " Send Test SMS (Admin Only)"),
                width = 6, status = "warning", solidHeader = TRUE,
                p(tags$small(icon("info-circle"),
                  " Send a test message to verify Twilio is working.",
                  " Uses the credentials configured in the Stream Alerts tab.")),
                textInput("test_sms_phone", "Recipient Phone (E.164)",
                          placeholder = "+13525551234", width = "100%"),
                textAreaInput("test_sms_msg", "Message",
                              value = "FPREN test message. If received, SMS delivery is working correctly. —FPREN",
                              rows = 3, width = "100%"),
                actionButton("btn_send_test_sms", "Send Test SMS",
                             class = "btn-warning", icon = icon("mobile-alt")),
                br(), br(),
                verbatimTextOutput("test_sms_status")
            ),
            # ── Group Invite Control ────────────────────────────────────────
            box(title = tagList(icon("users-cog"), " Group Invite Settings (Admin Only)"),
                width = 6, status = "info", solidHeader = TRUE,
                p(tags$small(icon("info-circle"),
                  " When disabled, users you add will NOT receive an invite email or SMS.",
                  " Their account is still created — you must distribute credentials manually.")),
                uiOutput("group_invite_toggle_ui"),
                br(),
                verbatimTextOutput("group_invite_status")
            )
          )
        ),

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
                " Check to include. Drag to reorder priority (top = highest). Unchecked types are silenced.")),
              # Sortable + checkable playlist editor
              tags$div(id = "playlist_sortable",
                style = "border:1px solid #ddd; border-radius:4px; padding:4px; background:#fff;",
                # Each row: drag handle + checkbox + label
                lapply(list(
                  list(val="fire",          label="Fire / Red Flag Warnings",  checked=TRUE),
                  list(val="flooding",      label="Flood Alerts",              checked=TRUE),
                  list(val="freeze",        label="Freeze / Winter Alerts",    checked=TRUE),
                  list(val="fog",           label="Fog Advisories",            checked=TRUE),
                  list(val="other_alerts",  label="Other Alerts",              checked=TRUE),
                  list(val="weather_report",label="Weather Reports",           checked=TRUE),
                  list(val="traffic",       label="Traffic Alerts",            checked=TRUE),
                  list(val="airport_weather",label="Airport Weather",          checked=TRUE),
                  list(val="educational",   label="Educational Content",       checked=TRUE),
                  list(val="imaging",       label="Imaging / Sweepers",        checked=TRUE),
                  list(val="top_of_hour",   label="Top of Hour IDs",           checked=TRUE)
                ), function(item) {
                  tags$div(
                    class = "playlist-row",
                    `data-val` = item$val,
                    style = "display:flex;align-items:center;padding:5px 8px;cursor:grab;border-bottom:1px solid #eee;",
                    tags$span(icon("grip-vertical"),
                              style = "color:#aaa;margin-right:8px;font-size:14px;flex-shrink:0;"),
                    tags$input(type="checkbox", id=paste0("pl_chk_",item$val),
                               value=item$val,
                               checked=if(item$checked) "checked" else NULL,
                               style="margin-right:8px;width:16px;height:16px;flex-shrink:0;"),
                    tags$label(`for`=paste0("pl_chk_",item$val),
                               style="margin:0;cursor:pointer;font-weight:normal;",
                               item$label)
                  )
                })
              ),
              tags$script(HTML("
                // SortableJS-based drag reorder for playlist rows
                (function() {
                  function initPlaylistSort() {
                    var el = document.getElementById('playlist_sortable');
                    if (!el || typeof Sortable === 'undefined') {
                      setTimeout(initPlaylistSort, 400);
                      return;
                    }
                    Sortable.create(el, {
                      animation: 150,
                      handle: '.fa-grip-vertical',
                      onEnd: function() {
                        // Collect ordered checked values and push to Shiny
                        var rows = el.querySelectorAll('.playlist-row');
                        var checked = [], all = [];
                        rows.forEach(function(r) {
                          var v = r.getAttribute('data-val');
                          all.push(v);
                          var cb = r.querySelector('input[type=checkbox]');
                          if (cb && cb.checked) checked.push(v);
                        });
                        Shiny.setInputValue('playlist_order', all.join(','));
                        Shiny.setInputValue('normal_playlist_types', checked);
                      }
                    });
                    // Also fire on checkbox change
                    el.addEventListener('change', function(e) {
                      if (e.target && e.target.type === 'checkbox') {
                        var rows = el.querySelectorAll('.playlist-row');
                        var checked = [], all = [];
                        rows.forEach(function(r) {
                          var v = r.getAttribute('data-val');
                          all.push(v);
                          var cb = r.querySelector('input[type=checkbox]');
                          if (cb && cb.checked) checked.push(v);
                        });
                        Shiny.setInputValue('playlist_order', all.join(','));
                        Shiny.setInputValue('normal_playlist_types', checked);
                      }
                    });
                  }
                  // Load SortableJS if not present
                  if (typeof Sortable === 'undefined') {
                    var s = document.createElement('script');
                    s.src = 'https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js';
                    s.onload = initPlaylistSort;
                    document.head.appendChild(s);
                  } else {
                    initPlaylistSort();
                  }
                })();
              ")),
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
          box(title = tagList(icon("file-pdf"), " Report Generator"), width = 12,
              status = "primary", solidHeader = TRUE,
              p(tags$small("Select a report template, configure its options, then generate a PDF.",
                           " All reports are saved to the output folder and can optionally be emailed.")),
              fluidRow(
                column(4,
                  selectInput("unified_rpt_template", "Report Template",
                    choices = list(
                      "NWS Alerts" = c(
                        "NWS Alert Summary (by zone & period)"  = "alert_summary",
                        "County Alert Report"                   = "county_alerts"
                      ),
                      "Weather" = c(
                        "Weather Trends (airport station)"      = "weather_trends"
                      ),
                      "Traffic" = c(
                        "Traffic Analysis Report"               = "traffic_analysis"
                      ),
                      "Census & Demographics" = c(
                        "Census Alert Population Impact"        = "census_impact"
                      ),
                      "Business Continuity" = c(
                        "BCP — General Facility"                = "bcp_general",
                        "BCP — Broadcast Facility & Staff"      = "bcp_broadcast",
                        "BCP — County Emergency Management"     = "bcp_county_em",
                        "BCP — Campus Police Force"             = "bcp_campus_police"
                      ),
                      "System" = c(
                        "Comprehensive FPREN System Report"     = "comprehensive"
                      )
                    ),
                    selected = "alert_summary")
                ),
                column(8,
                  uiOutput("unified_rpt_desc")
                )
              ),
              hr(),
              uiOutput("unified_rpt_params"),
              hr(),
              fluidRow(
                column(3,
                  checkboxInput("unified_rpt_email", "Email after generating", value = FALSE)
                ),
                column(3,
                  actionButton("btn_unified_gen", "Generate PDF",
                               class = "btn-primary btn-lg", icon = icon("file-pdf"))
                ),
                column(6,
                  verbatimTextOutput("unified_rpt_status")
                )
              )
          )
        ),
        fluidRow(
          box(title = "Recent Reports", width = 8, status = "info", solidHeader = TRUE,
              DTOutput("tbl_reports"),
              br(),
              uiOutput("rpt_download_links")
          ),
          box(title = "Scheduled Reports", width = 4, status = "success", solidHeader = TRUE,
              p(icon("clock"), strong(" Daily report runs automatically at 6:00 AM ET")),
              p("Reports are saved to:"),
              code("/home/ufuser/Fpren-main/reports/output/"),
              hr(),
              p("To run manually:"),
              code("Rscript reports/generate_and_email.R 7")
          )
        ),
        fluidRow(
          box(title = tagList(icon("shield-alt"), " Past BCP Reports"), width = 12,
              status = "warning", solidHeader = TRUE,
              p(tags$small(
                "Business Continuity Plan reports generated for your profile. ",
                "Admins see all users' reports. Select a row, then download or email."
              )),
              div(style = "overflow-x:auto; max-width:100%;",
                DTOutput("tbl_past_bcp_reports")
              ),
              br(),
              fluidRow(
                column(3,
                  downloadButton("dl_past_bcp_report", "Download Selected",
                                 class = "btn-sm btn-default", icon = icon("download"))
                ),
                column(3,
                  actionButton("btn_email_past_bcp_report", "Email Selected to Me",
                               class = "btn-sm btn-info", icon = icon("envelope"))
                ),
                column(6,
                  verbatimTextOutput("past_bcp_action_status")
                )
              )
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
              tags$span(
                title = paste0(
                  "Generates a PDF weather trends report for the selected airport station ",
                  "and date range. Includes temperature trend line chart, wind speed and ",
                  "direction rose, humidity trend, flight category distribution ",
                  "(VFR/MVFR/IFR/LIFR), summary statistics, and notable IFR/LIFR events. ",
                  "Uses hourly METAR snapshots stored in MongoDB (up to 90 days)."
                ),
                actionButton("btn_gen_wx_trend", "Generate Weather Trends PDF",
                             class = "btn-warning btn-lg", icon = icon("chart-line"))
              ),
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
        ),
        hr(),
        h3(icon("shield-alt"), " Business Continuity Plans"),
        fluidRow(
          box(title = "Generate Business Continuity Plan PDF", width = 6, status = "danger",
              solidHeader = TRUE,
              p(tags$small("Select a user and asset to generate a BCP. Choose",
                           strong("All Facilities"), "to generate individual BCPs for",
                           "every registered asset using each user's profession-matched template.")),
              selectInput("bcp_username", "User",
                choices = c("-- select a user --" = ""), selected = ""),
              selectInput("bcp_asset_id", "Asset / Property",
                choices = c("Select a user first" = ""), selected = ""),
              uiOutput("bcp_template_selector"),
              uiOutput("bcp_template_desc"),
              checkboxInput("bcp_email", "Email BCP after generating", value = FALSE),
              br(),
              tags$span(
                title = paste0(
                  "Generates a Business Continuity Plan PDF for the selected user asset. ",
                  "Includes live weather risk at the nearest airport, active NWS alerts ",
                  "for the asset's county, traffic and evacuation route data, census ",
                  "vulnerability analysis, emergency contacts, and a recovery timeline. ",
                  "Select 'All Facilities' to batch-generate BCPs for every asset."
                ),
                actionButton("btn_gen_bcp", "Generate BCP PDF",
                             class = "btn-danger btn-lg", icon = icon("shield-alt"))
              ),
              br(), br(),
              verbatimTextOutput("bcp_status")
          ),
          box(title = "Recent BCP Reports", width = 6, status = "info",
              solidHeader = TRUE,
              p(tags$small("Select a report row, then download or email it to your registered address.")),
              div(style = "overflow-x:auto; max-width:100%;",
                DTOutput("tbl_bcp_reports")
              ),
              br(),
              fluidRow(
                column(4,
                  downloadButton("dl_bcp_report", "Download", class = "btn-sm btn-default",
                                 icon = icon("download"))
                ),
                column(4,
                  actionButton("btn_email_bcp_report", "Email Me", class = "btn-sm btn-info",
                               icon = icon("envelope"))
                ),
                column(4,
                  verbatimTextOutput("bcp_report_action_status")
                )
              )
          )
        ),
        # ── Connectivity & Firewall Diagnostics ──────────────────────────────
        conditionalPanel(
          condition = "output.is_admin",
          fluidRow(
            box(
              title  = tagList(icon("network-wired"), " Connectivity & Firewall Diagnostics (Admin)"),
              width  = 12, status = "danger", solidHeader = TRUE, collapsible = TRUE,
              p(tags$small(
                icon("info-circle"),
                " Tests all local services, UF network endpoints, external APIs, and registered SNMP devices.",
                " Use this after context resets or to identify what UF IT needs to open.",
                " Each check makes one TCP/HTTP attempt per service — no polling."
              )),
              fluidRow(
                column(4,
                  actionButton("btn_gen_access_report",
                               "Run Connectivity Check & Generate PDF Report",
                               class = "btn-danger", icon = icon("stethoscope"),
                               width = "100%")
                ),
                column(3,
                  br(),
                  checkboxInput("access_report_email",
                                "Email report to me when done", value = FALSE)
                ),
                column(5,
                  verbatimTextOutput("access_report_status")
                )
              ),
              uiOutput("access_report_download_ui")
            )
          )
        )
      ),
      # ── Florida Rivers Alerts ────────────────────────────────────────────
      tabItem(tabName = "rivers",
        fluidRow(
          valueBoxOutput("rv_box_total",   width = 3),
          valueBoxOutput("rv_box_flood",   width = 3),
          valueBoxOutput("rv_box_worst",   width = 3),
          valueBoxOutput("rv_box_updated", width = 3)
        ),
        fluidRow(
          box(title = tagList(icon("water"), " River Gauge Status"),
              width = 8, status = "primary", solidHeader = TRUE,
              p(tags$small("Click a row to view the 24-hour trend chart below. Color = flood category.")),
              div(style = "overflow-x:auto;",
                DTOutput("tbl_river_gauges")
              ),
              br(),
              actionButton("btn_river_refresh", "Refresh", icon = icon("sync"),
                           class = "btn-sm btn-default")
          ),
          box(title = tagList(icon("robot"), " AI River Analysis"),
              width = 4, status = "info", solidHeader = TRUE,
              uiOutput("rv_ai_summary"),
              br(),
              conditionalPanel(
                condition = "output.is_admin",
                actionButton("btn_river_agent_run", "Run Agent Now",
                             icon = icon("play"), class = "btn-sm btn-warning"),
                verbatimTextOutput("rv_agent_status")
              )
          )
        ),
        fluidRow(
          box(title = uiOutput("rv_trend_title"),
              width = 12, status = "success", solidHeader = TRUE,
              plotlyOutput("rv_trend_chart", height = "280px")
          )
        )
      ),

      # ── Social Media ─────────────────────────────────────────────────────
      tabItem(tabName = "social_media",
        fluidRow(
          box(width = 12, status = "primary", solidHeader = TRUE,
              title = tagList(icon("share-nodes"), " Social Media Publishing — Coming Soon"),
              tags$div(style = "padding: 20px 0;",
                tags$h4(icon("clock"), " Planned Feature", style = "color:#007bff; margin-bottom:16px;"),
                tags$p("This tab will use LiteLLM AI agents to draft and publish emergency",
                       "alerts and community updates to all major social media platforms simultaneously,",
                       "including Nextdoor for hyper-local community reach."),
                tags$hr(),
                tags$h5("Planned Capabilities:", style = "margin-bottom:12px;"),
                tags$ul(style = "color:#555; line-height:2;",
                  tags$li(tags$strong("Platforms:"), " Facebook, Twitter/X, Instagram, LinkedIn, Nextdoor, Bluesky, Mastodon"),
                  tags$li(tags$strong("LiteLLM Agent:"), " Auto-drafts platform-appropriate posts from active NWS alerts (character limits, tone, hashtags per platform)"),
                  tags$li(tags$strong("Nextdoor integration:"), " Community-targeted posts by neighborhood/ZIP for localized emergency notices"),
                  tags$li(tags$strong("Post scheduler:"), " Queue posts for optimal engagement times or publish immediately for critical alerts"),
                  tags$li(tags$strong("Severity routing:"), " Extreme/Severe alerts post immediately; Moderate alerts queued for operator review"),
                  tags$li(tags$strong("Template library:"), " Pre-approved message templates for tornado, hurricane, flood, and heat events"),
                  tags$li(tags$strong("Engagement tracking:"), " Reach and engagement stats pulled back into the dashboard"),
                  tags$li(tags$strong("OAuth management:"), " Secure token storage per platform in MongoDB"),
                  tags$li(tags$strong("Audit log:"), " Full record of all posts sent, edited, or deleted")
                ),
                tags$hr(),
                tags$p(tags$em("Status: "), tags$strong("Pending development."),
                       " Requires OAuth app registration on each platform and LiteLLM agent workflow design.",
                       style = "color:#888; font-size:13px;")
              )
          )
        )
      ),

      # ── Travel Buddy ─────────────────────────────────────────────────────
      tabItem(tabName = "travel_buddy",
        fluidRow(
          box(width = 12, status = "success", solidHeader = TRUE,
              title = tagList(icon("route"), " Travel Buddy — Coming Soon"),
              tags$div(style = "padding: 20px 0;",
                tags$h4(icon("clock"), " Planned Feature", style = "color:#28a745; margin-bottom:16px;"),
                tags$p("Travel Buddy will combine airport delay data, Waze traffic incidents,",
                       "weather conditions, and historical travel patterns to recommend optimal",
                       "departure times and route planning — synced with companion iOS and Android apps."),
                tags$hr(),
                tags$h5("Planned Capabilities:", style = "margin-bottom:12px;"),
                tags$ul(style = "color:#555; line-height:2;",
                  tags$li(tags$strong("Departure time advisor:"), " AI-computed recommended leave time based on destination, flight time, and current conditions"),
                  tags$li(tags$strong("Airport integration:"), " Real-time FAA delay programs, TSA wait times, gate/terminal data for all Florida airports"),
                  tags$li(tags$strong("Waze traffic layer:"), " Accident, construction, and road closure data along the travel route"),
                  tags$li(tags$strong("Weather corridor analysis:"), " NWS alerts and radar for the entire route, not just origin/destination"),
                  tags$li(tags$strong("Push notifications:"), " Mobile app alerts when conditions change — earlier departure recommended, delay issued, etc."),
                  tags$li(tags$strong("User trip profiles:"), " Save home airport, preferred terminals, typical travel routes in user account"),
                  tags$li(tags$strong("iOS & Android apps:"), " Companion apps consuming a FPREN Travel Buddy REST API for on-the-go access"),
                  tags$li(tags$strong("Historical patterns:"), " Machine learning model trained on historical delay and traffic data per route/time of day"),
                  tags$li(tags$strong("Fleet/group travel:"), " Multi-user trip coordination for teams traveling together")
                ),
                tags$hr(),
                tags$p(tags$em("Status: "), tags$strong("Pending development."),
                       " Requires Travel Buddy REST API design, mobile app development, and FAA data source expansion.",
                       style = "color:#888; font-size:13px;")
              )
          )
        )
      ),

      # ── Alarms & SNMP ────────────────────────────────────────────────────
      tabItem(tabName = "alarms",
        fluidRow(
          box(width = 12, status = "danger", solidHeader = TRUE,
              title = tagList(icon("bell"), " Alarms & SNMP Monitoring — Coming Soon"),
              tags$div(style = "padding: 20px 0;",
                tags$h4(icon("clock"), " Planned Feature", style = "color:#dc3545; margin-bottom:16px;"),
                tags$p("This tab will provide a full SNMP-based monitoring and alarm management system,",
                       "watching Icecast streams, data feed health, and any connected SNMP-capable device",
                       "using standard MIBs and custom OIDs — alerting operators via SMS and email."),
                tags$hr(),
                tags$h5("Planned Capabilities:", style = "margin-bottom:12px;"),
                tags$ul(style = "color:#555; line-height:2;",
                  tags$li(tags$strong("SNMP polling:"), " SNMPv1/v2c/v3 polling of management devices using configurable MIBs and OID trees"),
                  tags$li(tags$strong("SNMP trap receiver:"), " Listen for inbound SNMP traps from network equipment, encoders, and broadcast hardware"),
                  tags$li(tags$strong("Icecast stream watchdog:"), " Alarm when any stream mount goes offline, bitrate drops, or listener count anomaly detected"),
                  tags$li(tags$strong("Data feed watchdog:"), " Alert when NWS fetcher, Waze fetcher, METAR, or any MongoDB collection stops updating"),
                  tags$li(tags$strong("Custom OID alarms:"), " User-defined thresholds on any polled OID value (CPU, temperature, signal level, etc.)"),
                  tags$li(tags$strong("Alarm severity levels:"), " Critical, Major, Minor, Warning — each with configurable notification rules"),
                  tags$li(tags$strong("SMS notifications:"), " Twilio SMS to configured on-call numbers for Critical and Major alarms"),
                  tags$li(tags$strong("Email notifications:"), " SMTP email for all alarm levels with alarm details and suggested remediation"),
                  tags$li(tags$strong("Alarm dashboard:"), " Real-time alarm list, acknowledge/clear workflow, and escalation timers"),
                  tags$li(tags$strong("Alarm history:"), " Full audit trail of alarm events, acknowledgements, and resolutions in MongoDB"),
                  tags$li(tags$strong("Maintenance windows:"), " Suppress alarms during scheduled maintenance to prevent false notifications"),
                  tags$li(tags$strong("MIB browser:"), " Built-in MIB upload and OID tree browser for device discovery")
                ),
                tags$hr(),
                tags$p(tags$em("Status: "), tags$strong("Pending development."),
                       " Requires SNMP daemon (Net-SNMP), trap receiver service, and alarm rules engine implementation.",
                       style = "color:#888; font-size:13px;")
              )
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

  # Ensure TinyTeX binaries (xelatex, etc.) are on PATH for PDF rendering
  Sys.setenv(PATH = paste0("/home/ufuser/.local/bin:", Sys.getenv("PATH")))

  # Profession → BCP template auto-mapping
  PROFESSION_TEMPLATE_MAP <- c(
    "Broadcast Engineer"             = "broadcast",
    "Broadcast Administrator"        = "broadcast",
    "Broadcast Operator"             = "broadcast",
    "Program Director"               = "broadcast",
    "Chief Engineer"                 = "broadcast",
    "News Director"                  = "broadcast",
    "Production Manager"             = "broadcast",
    "Police Chief"                   = "campus_police",
    "Police Lieutenant"              = "campus_police",
    "Police Officer"                 = "campus_police",
    "Campus Security Officer"        = "campus_police",
    "Dispatch Coordinator"           = "campus_police",
    "Emergency Services Coordinator" = "campus_police",
    "County Emergency Manager"       = "county_em",
    "City Administrator"             = "county_em",
    "Public Safety Director"         = "county_em",
    "EOC Coordinator"                = "county_em",
    "FEMA Liaison"                   = "county_em",
    "Hazmat Coordinator"             = "county_em",
    "Facility Manager"               = "general",
    "IT/Systems Administrator"       = "general",
    "Station Manager"                = "general",
    "Other"                          = "general"
  )

  # ── Auth reactive state ──────────────────────────────────────────────────────
  auth_rv <- reactiveValues(
    logged_in   = FALSE,
    username    = NULL,
    role        = NULL,
    email       = NULL,
    phone       = NULL,
    user_doc    = NULL
  )

  login_msg_rv         <- reactiveVal("")
  invite_user_rv       <- reactiveVal(NULL)   # user doc for pending invite
  invite_landing_rv    <- reactiveVal("")     # message shown on Panel A
  invite_accept_rv     <- reactiveVal("")     # message shown on Panel B

  output$invite_landing_msg <- renderUI({
    msg <- invite_landing_rv()
    if (nchar(msg) == 0) return(NULL)
    tags$div(class = "alert alert-warning",
             style = "font-size:13px; margin:8px 0;", HTML(msg))
  })
  output$invite_accept_msg <- renderUI({
    u <- invite_user_rv()
    msg <- invite_accept_rv()
    tagList(
      if (!is.null(u))
        tags$p(style = "color:#555;font-size:13px;",
          "Welcome, ", tags$strong(as.character(u$username %||% "")), "!",
          " Set a password to complete your registration.")
      else NULL,
      if (nchar(msg) > 0)
        tags$div(class = "alert alert-warning",
                 style = "font-size:12px;", HTML(msg))
      else NULL
    )
  })

  # ── Read invite token from URL on session start ─────────────────────────────
  observe({
    query <- parseQueryString(isolate(session$clientData$url_search))
    tok   <- query[["invite"]] %||% ""
    if (nchar(tok) == 0) return()

    col <- tryCatch(mongo("users", "weather_rss", url = MONGO_URI),
                    error = function(e) NULL)
    if (is.null(col)) return()

    u_row <- tryCatch({
      r <- col$find(sprintf('{"invite_token":"%s"}', tok))
      col$disconnect()
      if (nrow(r) > 0) r[1, ] else NULL
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL); NULL
    })

    if (is.null(u_row)) {
      invite_landing_rv("Invalid or already-used invitation link.")
      return()
    }

    # Check expiry
    exp_str <- tryCatch(as.character(u_row$invite_expires %||% ""),
                        error = function(e) "")
    if (nchar(exp_str) > 0) {
      exp_t <- tryCatch(as.POSIXct(exp_str, tz = "UTC"), error = function(e) NA)
      if (!is.na(exp_t) && Sys.time() > exp_t) {
        # Delete expired account
        col2 <- tryCatch(mongo("users", "weather_rss", url = MONGO_URI),
                         error = function(e) NULL)
        if (!is.null(col2)) {
          tryCatch({
            col2$remove(sprintf('{"invite_token":"%s"}', tok))
            col2$disconnect()
          }, error = function(e)
            tryCatch(col2$disconnect(), error = function(e2) NULL))
        }
        log_audit("user_deleted", as.character(u_row$username %||% "unknown"),
                  "system", "Invite expired — account auto-deleted")
        invite_landing_rv(paste0(
          "<strong>This invitation has expired.</strong> ",
          "Your provisional account has been removed. ",
          "Please contact <a href='mailto:lawrence.bornace@ufl.edu'>",
          "lawrence.bornace@ufl.edu</a> to request a new invite."
        ))
        return()
      }
    }

    # Valid invite — show Panel B
    invite_user_rv(u_row)
    shinyjs::hide("panel_invite_only")
    shinyjs::show("panel_invite_accept")
  })

  # ── Accept invite: set password, auto-login ─────────────────────────────────
  observeEvent(input$btn_accept_invite, {
    u <- invite_user_rv()
    if (is.null(u)) {
      invite_accept_rv("Session lost — please use your invite link again."); return()
    }
    pw1 <- input$invite_pw1 %||% ""
    pw2 <- input$invite_pw2 %||% ""
    if (nchar(pw1) < 8) {
      invite_accept_rv("Password must be at least 8 characters."); return()
    }
    if (pw1 != pw2) {
      invite_accept_rv("Passwords do not match."); return()
    }

    uname    <- as.character(u$username %||% "")
    new_hash <- bcrypt::hashpw(pw1)
    now_str  <- format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")

    col <- tryCatch(mongo("users", "weather_rss", url = MONGO_URI),
                    error = function(e) NULL)
    if (is.null(col)) {
      invite_accept_rv("Database unavailable — try again in a moment."); return()
    }
    tryCatch({
      col$update(
        sprintf('{"username":"%s"}', uname),
        sprintf('{"$set":{"password":"%s","must_change_password":false,"invite_token":null,"invite_expires":null,"last_login":"%s"}}',
                new_hash, now_str)
      )
      col$disconnect()
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL)
      invite_accept_rv(paste0("Error activating account: ", conditionMessage(e)))
      return()
    })

    log_audit("invite_accepted", uname, uname, "Account activated via invite link")

    # Auto-login
    auth_rv$logged_in <- TRUE
    auth_rv$username  <- uname
    auth_rv$role      <- as.character(u$role  %||% "viewer")
    auth_rv$email     <- as.character(u$email %||% "")
    auth_rv$phone     <- as.character(u$phone %||% "")
    auth_rv$user_doc  <- u
    shinyjs::hide("login_screen")
    shinyjs::show("main_dashboard")

    # Trigger post-login verification flow
    must_change <- FALSE   # just changed password, skip that step
    phone_ver   <- isTRUE(u$phone_verified)
    email_ver   <- isTRUE(u$email_verified)
    if (!phone_ver && nchar(auth_rv$phone) > 0) {
      code     <- paste0(sample(0:9, 6, replace = TRUE), collapse = "")
      exp_time <- format(Sys.time() + 600, "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
      col2 <- tryCatch(mongo("users", "weather_rss", url = MONGO_URI),
                       error = function(e) NULL)
      if (!is.null(col2)) {
        tryCatch({
          col2$update(sprintf('{"username":"%s"}', uname),
                      sprintf('{"$set":{"verify_code":"%s","verify_expires":"%s"}}',
                              code, exp_time))
          col2$disconnect()
        }, error = function(e)
          tryCatch(col2$disconnect(), error = function(e2) NULL))
      }
      send_twilio_sms(auth_rv$phone,
        paste0("FPREN: Your verification code is ", code,
               ". Expires in 10 minutes. —FPREN"))
      showModal(.phone_verify_modal())
    }
  })

  output$is_admin <- reactive({ isTRUE(auth_rv$role == "admin") })
  outputOptions(output, "is_admin", suspendWhenHidden = FALSE)

  # ── Role-based sidebar ────────────────────────────────────────────────────────
  # viewer  : monitoring tabs only (read-only)
  # operator: viewer + Upload, Reports, Station Health, Zones
  # admin   : everything + Config / User Management
  output$sidebar_menu <- renderMenu({
    role <- if (!is.null(auth_rv$role)) auth_rv$role else "viewer"
    viewer_items <- list(
      menuItem("Overview",                 tabName = "overview",        icon = icon("tachometer-alt")),
      menuItem("Weather Conditions",       tabName = "wx_cities",       icon = icon("cloud-sun")),
      menuItem("FL Alerts",                tabName = "alerts",          icon = icon("exclamation-triangle")),
      menuItem("Traffic Alerts",           tabName = "traffic_alerts",  icon = icon("car-crash")),
      menuItem("Traffic Analysis",         tabName = "traffic_analysis",icon = icon("chart-bar")),
      menuItem("County Alerts",            tabName = "county_alerts",   icon = icon("map-marker-alt")),
      menuItem("Airport Delays & Weather", tabName = "airports",        icon = icon("plane")),
      menuItem("Icecast Streams",          tabName = "icecast",         icon = icon("broadcast-tower")),
      menuItem("Feed Status",              tabName = "feeds",           icon = icon("rss")),
      menuItem("Census & Demographics",    tabName = "census",          icon = icon("users")),
      menuItem("Florida Rivers Alerts",   tabName = "rivers",          icon = icon("water")),
      menuItem("Social Media",            tabName = "social_media",    icon = icon("share-nodes")),
      menuItem("Travel Buddy",            tabName = "travel_buddy",    icon = icon("route")),
      menuItem("Alarms & SNMP",           tabName = "alarms",          icon = icon("bell"))
    )
    operator_items <- list(
      menuItem("Upload Content",  tabName = "upload",   icon = icon("upload")),
      menuItem("Reports",         tabName = "reports",  icon = icon("file-pdf")),
      menuItem("Station Health",  tabName = "health",   icon = icon("heartbeat")),
      menuItem("Zones",           tabName = "zones",    icon = icon("map"))
    )
    admin_items <- list(
      menuItem("Config",          tabName = "config",   icon = icon("cog"))
    )
    items <- viewer_items
    if (role %in% c("operator", "admin")) items <- c(items, operator_items)
    if (role == "admin")                  items <- c(items, admin_items)
    do.call(sidebarMenu, items)
  })

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
      if (!is.na(ll) && difftime(now_utc, ll, units = "days") > 180) {
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
        "dot_district":1,"last_updated":1,"_id":0}')
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

  # ── SNMP / System Status ─────────────────────────────────────────────────────
  snmp_timer    <- reactiveTimer(60000)
  snmp_status_rv <- reactiveVal(0)

  snmp_status <- reactive({
    snmp_timer()
    snmp_status_rv()
    col <- tryCatch(
      mongo(collection = "fpren_snmp_status", db = "weather_rss", url = MONGO_URI),
      error = function(e) NULL)
    if (is.null(col)) return(NULL)
    tryCatch({
      r <- col$find('{"_id":"singleton"}')
      col$disconnect()
      if (nrow(r) == 0) NULL else r[1, ]
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL); NULL
    })
  })

  observeEvent(input$btn_snmp_refresh, { snmp_status_rv(snmp_status_rv() + 1) })

  .snmp_val <- function(field, default = "UNKNOWN") {
    d <- snmp_status()
    v <- tryCatch(d[[field]], error = function(e) NULL)
    if (is.null(v) || length(v) == 0 || (length(v) == 1 && is.na(v))) default else v
  }

  output$snmp_box_health <- renderValueBox({
    h <- .snmp_val("system_health", "UNKNOWN")
    color <- switch(h, OK = "green", DEGRADED = "yellow", CRITICAL = "red", "light-blue")
    valueBox(h, "System Health", icon = icon("heartbeat"), color = color)
  })

  output$snmp_box_services <- renderValueBox({
    n <- as.integer(.snmp_val("active_service_count", 0))
    color <- if (n >= 11) "green" else if (n >= 8) "yellow" else "red"
    valueBox(paste0(n, " / 11"), "Active Services", icon = icon("cogs"), color = color)
  })

  output$snmp_box_alerts <- renderValueBox({
    n <- as.integer(.snmp_val("active_alert_count", 0))
    color <- if (n == 0) "green" else if (n < 5) "yellow" else "red"
    valueBox(n, "Active NWS Alerts", icon = icon("exclamation-triangle"), color = color)
  })

  output$snmp_box_wx_cat <- renderValueBox({
    cat <- .snmp_val("worst_flight_cat", "UNK")
    color <- switch(cat, VFR = "green", MVFR = "yellow", IFR = "orange", LIFR = "red", "light-blue")
    valueBox(cat, "Worst Wx Category", icon = icon("plane"), color = color)
  })

  output$tbl_snmp_services <- renderDT({
    d <- snmp_status()
    if (is.null(d) || is.null(d$services) || length(d$services) == 0) {
      return(datatable(
        data.frame(Message = "SNMP updater not yet run — check fpren-snmp-updater.timer"),
        options = list(dom = "t"), rownames = FALSE))
    }
    svcs <- d$services[[1]]
    if (!is.data.frame(svcs)) svcs <- tryCatch(as.data.frame(do.call(rbind, lapply(svcs, as.list))), error = function(e) data.frame())
    if (nrow(svcs) == 0) return(datatable(data.frame(Message = "No service data"), options = list(dom="t"), rownames=FALSE))
    svcs_disp <- svcs[, intersect(c("name","status","oid"), names(svcs)), drop=FALSE]
    updated <- tryCatch(as.character(d$last_cache_update), error = function(e) "")
    datatable(svcs_disp,
      caption  = if (nchar(updated) > 0) paste("Last updated:", updated) else NULL,
      options  = list(pageLength = 11, dom = "t", scrollX = TRUE),
      rownames = FALSE) %>%
      formatStyle("status",
        backgroundColor = styleEqual(
          c("active","inactive","failed","unknown"),
          c("#dff0d8","#fcf8e3","#f2dede","#d9edf7")))
  })

  output$tbl_snmp_asset_oids <- renderDT({
    d <- snmp_status()
    if (is.null(d) || is.null(d$asset_oid_map) || length(d$asset_oid_map) == 0) {
      return(datatable(data.frame(Message = "No assets registered"),
                       options = list(dom = "t"), rownames = FALSE))
    }
    oid_list <- d$asset_oid_map[[1]]
    if (!is.data.frame(oid_list)) oid_list <- tryCatch(as.data.frame(do.call(rbind, lapply(oid_list, as.list))), error = function(e) data.frame())
    datatable(oid_list,
      options  = list(pageLength = 10, dom = "t", scrollX = TRUE),
      rownames = FALSE)
  })

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
          `data-icao` = row$icao,
          `data-lat`  = as.character(row$lat),
          `data-lon`  = as.character(row$lon),
          style = "position:relative; cursor:pointer;",
          div(class = "wx-city", row$display_name,
              tags$span(style = "font-size:10px; color:#aaa; margin-left:4px;",
                        title = "Hover for 7-day forecast", icon("calendar-alt"))),
          div(class = "wx-cat",  cat),
          div(class = "wx-temp", temp_f),
          div(class = "wx-feels", feels_str),
          if (nchar(wx_desc) > 0) div(class = "wx-desc", wx_desc),
          div(class = "wx-detail",
            icon("wind"), wind_str, tags$br(),
            icon("tint"), hum_str, tags$br(),
            icon("eye"),  vis_str),
          div(class = "wx-time", icon("clock"), " Obs: ", obs_str),
          # 7-day forecast popup (hidden by default, shown on hover)
          div(class = "wx-forecast-popup",
              style = paste0(
                "display:none; position:absolute; z-index:9999;",
                " top:0; left:100%; min-width:320px; max-width:400px;",
                " background:#fff; border:2px solid #0077aa;",
                " border-radius:6px; padding:10px; box-shadow:2px 4px 12px rgba(0,0,0,0.25);",
                " font-size:12px; line-height:1.4;"
              ),
              div(style = "font-weight:bold; color:#003087; margin-bottom:6px;",
                  paste0(row$display_name, " — 7-Day Forecast")),
              div(class = "wx-forecast-content",
                  style = "color:#666;", "Loading forecast...")
          )
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
    # Append 7-day hover JS after the grid renders
    tagList(
      do.call(tagList, rows),
      tags$script(HTML("
        (function() {
          // NWS 7-day forecast hover for city weather cards
          var _fcCache = {};

          function fetchForecast(lat, lon, popup, contentEl) {
            var key = lat + ',' + lon;
            if (_fcCache[key]) { renderForecast(contentEl, _fcCache[key]); return; }
            contentEl.innerHTML = 'Loading forecast...';
            fetch('https://api.weather.gov/points/' + lat + ',' + lon)
              .then(function(r){ return r.json(); })
              .then(function(d){
                var furl = d && d.properties && d.properties.forecast;
                if (!furl) throw new Error('No forecast URL');
                return fetch(furl);
              })
              .then(function(r){ return r.json(); })
              .then(function(d){
                var periods = d && d.properties && d.properties.periods;
                if (!periods) throw new Error('No periods');
                _fcCache[key] = periods.slice(0, 14);
                renderForecast(contentEl, _fcCache[key]);
              })
              .catch(function(e){
                contentEl.innerHTML = '<span style=\"color:#c00;\">Forecast unavailable</span>';
              });
          }

          function renderForecast(el, periods) {
            var html = '<div style=\"display:flex; flex-wrap:wrap; gap:4px;\">';
            // Show day-time periods only (max 7)
            var days = periods.filter(function(p){ return p.isDaytime; }).slice(0, 7);
            days.forEach(function(p) {
              var tempClr = p.temperature > 90 ? '#c00' : p.temperature < 45 ? '#00c' : '#333';
              html += '<div style=\"flex:0 0 calc(14% - 4px); min-width:38px; text-align:center;' +
                      'border:1px solid #ddd; border-radius:4px; padding:3px 2px; background:#f9f9f9;\">' +
                '<div style=\"font-weight:bold; font-size:10px; color:#003087;\">' +
                  p.name.replace('This ', '').substring(0,3) + '</div>' +
                '<div style=\"font-size:11px; color:' + tempClr + '; font-weight:bold;\">' +
                  p.temperature + '&deg;' + p.temperatureUnit + '</div>' +
                '<div style=\"font-size:9px; color:#555; line-height:1.2;\">' +
                  p.shortForecast.substring(0, 22) + '</div>' +
                '</div>';
            });
            html += '</div>';
            el.innerHTML = html;
          }

          function attachHovers() {
            var cards = document.querySelectorAll('.wx-card[data-lat]');
            cards.forEach(function(card) {
              if (card._hoverAttached) return;
              card._hoverAttached = true;
              var lat = card.getAttribute('data-lat');
              var lon = card.getAttribute('data-lon');
              var popup = card.querySelector('.wx-forecast-popup');
              var content = popup && popup.querySelector('.wx-forecast-content');
              if (!popup || !content) return;

              var timer = null;
              card.addEventListener('mouseenter', function() {
                timer = setTimeout(function() {
                  // Flip popup to left side if near right edge
                  var rect = card.getBoundingClientRect();
                  if (rect.right + 320 > window.innerWidth) {
                    popup.style.left = 'auto';
                    popup.style.right = '100%';
                  } else {
                    popup.style.left = '100%';
                    popup.style.right = 'auto';
                  }
                  popup.style.display = 'block';
                  fetchForecast(lat, lon, popup, content);
                }, 300);
              });
              card.addEventListener('mouseleave', function() {
                clearTimeout(timer);
                popup.style.display = 'none';
              });
            });
          }

          // Re-attach after Shiny re-renders the grid
          var observer = new MutationObserver(function() { attachHovers(); });
          observer.observe(document.body, { childList: true, subtree: true });
          attachHovers();
        })();
      "))
    )
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

  # ── Traffic Alerts map ───────────────────────────────────────────────────────

  output$traffic_map <- leaflet::renderLeaflet({
    df <- traffic_filtered()

    base_map <- leaflet::leaflet() %>%
      leaflet::addProviderTiles(leaflet::providers$CartoDB.Positron) %>%
      leaflet::setView(lng = -83.5, lat = 27.8, zoom = 7)

    if (nrow(df) == 0 || !"county" %in% names(df)) return(base_map)

    # Aggregate by county
    county_df <- df %>%
      filter(!is.na(county), county != "") %>%
      group_by(county) %>%
      summarise(
        incidents  = n(),
        major      = sum(tolower(severity) == "major",        na.rm = TRUE),
        closures   = sum(is_full_closure == TRUE,             na.rm = TRUE),
        top_type   = names(sort(table(type), decreasing = TRUE))[1],
        .groups    = "drop"
      ) %>%
      mutate(
        worst = case_when(major > 0 ~ "Major", closures > 0 ~ "Closure", TRUE ~ "Minor"),
        fill  = case_when(worst == "Major" ~ "#bd0026", worst == "Closure" ~ "#fd8d3c", TRUE ~ "#feb24c")
      )

    map_df <- FL_COUNTY_LATLON %>%
      inner_join(county_df, by = "county")

    if (nrow(map_df) == 0) return(base_map)

    radius_fn <- function(n) scales::rescale(sqrt(n), to = c(10, 50),
                                              from = c(1, sqrt(max(n, 1))))

    base_map %>%
      leaflet::addCircleMarkers(
        data        = map_df,
        lat         = ~lat,
        lng         = ~lon,
        layerId     = ~county,
        radius      = ~radius_fn(incidents),
        color       = "white",
        weight      = 1.5,
        fillColor   = ~fill,
        fillOpacity = 0.85,
        popup = ~paste0(
          "<b>", county, " County</b><br>",
          "<b>", incidents, " incident", ifelse(incidents == 1, "", "s"), "</b>",
          ifelse(major > 0,   paste0("<br><span style='color:#bd0026;'>&#9679; ", major, " Major</span>"), ""),
          ifelse(closures > 0, paste0("<br><span style='color:#fd8d3c;'>&#9679; ", closures, " Full Closure(s)</span>"), ""),
          ifelse(!is.na(top_type), paste0("<br>Top type: ", top_type), ""),
          "<br><small><i>Click to filter table</i></small>"
        ),
        label = ~paste0(county, ": ", incidents, " incident", ifelse(incidents == 1, "", "s"))
      ) %>%
      leaflet::addLegend(
        position = "bottomright",
        colors   = c("#bd0026","#fd8d3c","#feb24c"),
        labels   = c("Major incident","Full closure","Minor/Other"),
        title    = "Severity",
        opacity  = 0.85
      )
  })

  # Click on county circle → update the County filter dropdown
  observeEvent(input$traffic_map_marker_click, {
    clicked <- input$traffic_map_marker_click$id
    if (!is.null(clicked) && nchar(clicked) > 0) {
      updateSelectInput(session, "traffic_county", selected = clicked)
    }
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
    county <- input$ca_pdf_county
    if (is.null(county) || county == "") {
      showNotification("Select a county for the report.", type = "warning")
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
    county <- input$ca_pdf_county
    if (is.null(county) || county == "") {
      showNotification("Select a county for the report.", type = "warning")
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

  # ── Inovonics 677 EAS Monitor ────────────────────────────────────────────────
  eas_rv         <- reactiveVal(NULL)
  eas_poll_timer <- reactiveTimer(300000)  # auto-refresh every 5 minutes

  # Load from MongoDB on tab visit or timer tick
  eas_data <- reactive({
    eas_poll_timer()
    eas_rv()   # also invalidates on manual poll
    col <- tryCatch(mongo(collection="eas_monitor", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) return(NULL)
    tryCatch({
      r <- col$find('{"_id":"singleton"}')
      col$disconnect()
      if (nrow(r) == 0) NULL else r[1, ]
    }, error=function(e) { tryCatch(col$disconnect(), error=function(e2) NULL); NULL })
  })

  # Poll Now button — runs the Python poller script
  observeEvent(input$btn_eas_poll, {
    showNotification("Polling Inovonics 677...", type="message", duration=3)
    result <- tryCatch(
      system2("python3",
              args = c("/home/ufuser/Fpren-main/scripts/inovonics_poller.py",
                       "--host", "10.245.74.39", "--community", "public"),
              stdout=TRUE, stderr=TRUE, timeout=20),
      error=function(e) paste("ERROR:", e$message)
    )
    eas_rv(Sys.time())   # trigger reactive refresh
    if (any(grepl("Reachable: True", result)))
      showNotification("Device online — data updated.", type="message")
    else
      showNotification("Device unreachable. Showing last known data.", type="warning")
  })

  # Device status badge
  output$eas_device_status_ui <- renderUI({
    d <- eas_data()
    if (is.null(d)) {
      return(tags$span(class="label label-default", "No data"))
    }
    if (isTRUE(d$reachable)) {
      tagList(
        tags$span(class="label label-success", style="font-size:14px;padding:6px 12px;",
                  icon("check-circle"), " ONLINE"),
        tags$span(style="margin-left:10px; color:#555;",
                  if (!is.null(d$device_info) && !is.null(d$device_info$sysDescr))
                    as.character(d$device_info$sysDescr) else "Inovonics 677")
      )
    } else {
      tags$span(class="label label-danger", style="font-size:14px;padding:6px 12px;",
                icon("times-circle"), " OFFLINE / UNREACHABLE")
    }
  })

  # Last poll timestamp
  output$eas_last_poll_ui <- renderUI({
    d <- eas_data()
    if (is.null(d) || is.null(d$polled_at)) return(tags$span(style="color:#888;", "Never"))
    ts <- tryCatch(lubridate::ymd_hms(as.character(d$polled_at)), error=function(e) NULL)
    if (is.null(ts)) return(tags$span(style="color:#888;", as.character(d$polled_at)))
    local_ts <- format(ts, "%Y-%m-%d %H:%M:%S", tz="America/New_York")
    tags$span(style="color:#888; font-size:12px;", local_ts, " ET")
  })

  # Station cards for the 3 sources
  output$eas_sources_ui <- renderUI({
    d <- eas_data()
    if (is.null(d) || is.null(d$sources)) {
      return(p(style="color:#888;", "No source data. Click Poll Now to query the device."))
    }

    # d$sources is a list (mongolite returns nested lists)
    sources <- tryCatch(d$sources[[1]], error=function(e) d$sources)
    if (is.data.frame(sources)) {
      src_list <- lapply(seq_len(nrow(sources)), function(i) as.list(sources[i, ]))
    } else if (is.list(sources)) {
      src_list <- sources
    } else {
      return(p("Could not parse source data."))
    }

    cards <- lapply(src_list, function(src) {
      eas_active <- as.character(src$eas_active %||% "Unknown")
      card_color <- if (eas_active == "ALERT") "#c0392b" else if (eas_active == "Normal") "#27ae60" else "#888"
      card_bg    <- if (eas_active == "ALERT") "#fdf2f2" else "#f9f9f9"
      eas_badge  <- if (eas_active == "ALERT")
        tags$span(class="label label-danger",  style="font-size:13px;", icon("exclamation-triangle"), " EAS ALERT ACTIVE")
      else if (eas_active == "Normal")
        tags$span(class="label label-success", style="font-size:12px;", icon("check"), " Normal")
      else
        tags$span(class="label label-default", style="font-size:12px;", "Unknown")

      column(4,
        div(style=paste0("background:", card_bg, ";border:2px solid ", card_color,
                         ";border-radius:8px;padding:14px;margin-bottom:12px;"),
          h4(style=paste0("margin-top:0;color:", card_color, ";"),
             icon("radio"), " Source ", src$source_num %||% "?",
             tags$small(style="font-weight:normal;margin-left:8px;color:#555;",
                        as.character(src$label %||% ""))),
          tags$table(style="width:100%;font-size:13px;",
            tags$tr(
              tags$td(style="color:#666;padding:2px 8px 2px 0;", "Frequency:"),
              tags$td(style="font-weight:bold;", as.character(src$frequency %||% "Unknown"))
            ),
            tags$tr(
              tags$td(style="color:#666;padding:2px 8px 2px 0;", "Signal (RSSI):"),
              tags$td(as.character(src$rssi_dbm %||% "Unknown"))
            ),
            tags$tr(
              tags$td(style="color:#666;padding:2px 8px 2px 0;", "Stereo:"),
              tags$td(as.character(src$stereo %||% "Unknown"))
            ),
            tags$tr(
              tags$td(style="color:#666;padding:2px 8px 2px 0;", "Audio:"),
              tags$td(as.character(src$audio %||% "Unknown"))
            ),
            tags$tr(
              tags$td(style="color:#666;padding:2px 8px 2px 0;", "RDS:"),
              tags$td(as.character(src$rds %||% "Unknown"))
            )
          ),
          hr(style="margin:8px 0;"),
          div(style="text-align:center;", eas_badge),
          if (nchar(as.character(src$eas_msg %||% "")) > 0)
            div(style="margin-top:6px;font-size:11px;color:#555;background:#fff;padding:6px;border-radius:4px;word-break:break-all;",
                icon("info-circle"), " ", as.character(src$eas_msg))
        )
      )
    })
    fluidRow(cards)
  })

  # Raw OID walk table (admin only)
  output$tbl_eas_raw_oids <- DT::renderDataTable({
    d <- eas_data()
    if (is.null(d) || is.null(d$raw_oid_walk)) return(data.frame(OID="No walk data yet"))
    walk <- d$raw_oid_walk
    if (length(walk) == 0) return(data.frame(Message="Device was unreachable — no OID walk available"))
    # walk may come back as a nested list from mongolite
    if (is.data.frame(walk)) {
      df <- walk
    } else {
      oids <- names(walk)
      vals <- unlist(walk)
      df <- data.frame(OID = oids, Value = vals, stringsAsFactors = FALSE)
    }
    DT::datatable(df, options = list(pageLength=20, scrollX=TRUE), rownames=FALSE)
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
  rpt_status_msg     <- reactiveVal("")
  unified_rpt_status <- reactiveVal("")
  rpt_output_dir     <- "/home/ufuser/Fpren-main/reports/output"

  output$rpt_status        <- renderText({ rpt_status_msg() })
  output$unified_rpt_status <- renderText({ unified_rpt_status() })

  # Template descriptions
  .RPT_DESCS <- list(
    alert_summary   = list(icon="bell",         color="#2471a3",
      text="Summarizes all NWS alerts for a zone over a selected time period. Includes severity breakdown, event type distribution, and per-alert detail with issued/expires times."),
    county_alerts   = list(icon="map-marker-alt",color="#1a6b3a",
      text="All active NWS alerts for a specific Florida county or all of Florida. Includes severity table, per-alert descriptions, and county-level summary statistics."),
    weather_trends  = list(icon="chart-line",    color="#d68910",
      text="Historical weather trends for any FL airport station over a date range. Shows temperature trend, wind rose, humidity, and flight category distribution (VFR/MVFR/IFR/LIFR)."),
    traffic_analysis= list(icon="car",           color="#884ea0",
      text="FL511 traffic incident analysis for a selected county and date. Includes incident type breakdown, map-ready location data, and DOT district summary."),
    census_impact   = list(icon="users",         color="#e74c3c",
      text="Census-based population impact assessment for a selected NWS alert. Shows total population at risk, county demographics (elderly %, poverty %, disability %), and AI-generated impact narrative."),
    bcp_general     = list(icon="building",      color="#566573",
      text="General facility Business Continuity Plan. Covers weather risk at nearest airport, active alerts for the county, traffic/evacuation routes, census vulnerability data, and recovery timeline."),
    bcp_broadcast   = list(icon="broadcast-tower",color="#e07020",
      text="Broadcast Facility & Staff BCP. Adds equipment checklist (transmitter, generator, Icecast, UPS), FCC emergency obligations, on-air continuity options, and staff accountability roles."),
    bcp_county_em   = list(icon="map",           color="#1a6b3a",
      text="County Emergency Management BCP. Covers evacuation zone thresholds by hurricane category, EOC activation levels, mass notification coordination (FPREN/IPAWS/CodeRED/EAS), and FEMA recovery timeline."),
    bcp_campus_police=list(icon="shield-alt",    color="#1c2b6e",
      text="Campus Police Force BCP. Includes sector assignments, staffing levels by alert phase, vulnerable population protocols (ADA/LEP), inter-agency MOU checklist, and vehicle pre-positioning."),
    comprehensive   = list(icon="th-list",       color="#212f3d",
      text="Full FPREN system report. Covers all active alerts statewide, weather obs for 16 FL cities, stream health, traffic incidents, and BCP for all registered user assets.")
  )

  output$unified_rpt_desc <- renderUI({
    tmpl <- input$unified_rpt_template %||% "alert_summary"
    d    <- .RPT_DESCS[[tmpl]]
    if (is.null(d)) return(NULL)
    div(style=paste0("border-left:4px solid ", d$color, ";padding:8px 12px;background:#fafafa;border-radius:0 4px 4px 0;"),
      tags$small(icon(d$icon), " ", d$text)
    )
  })

  # FL counties list (shared)
  .FL_COUNTIES_LIST <- c("All Florida",
    sort(c("Alachua","Baker","Bay","Bradford","Brevard","Broward",
      "Calhoun","Charlotte","Citrus","Clay","Collier","Columbia",
      "DeSoto","Dixie","Duval","Escambia","Flagler","Franklin",
      "Gadsden","Gilchrist","Glades","Gulf","Hamilton","Hardee",
      "Hendry","Hernando","Highlands","Hillsborough","Holmes",
      "Indian River","Jackson","Jefferson","Lafayette","Lake",
      "Lee","Leon","Levy","Liberty","Madison","Manatee","Marion",
      "Martin","Miami-Dade","Monroe","Nassau","Okaloosa","Okeechobee",
      "Orange","Osceola","Palm Beach","Pasco","Pinellas","Polk",
      "Putnam","St. Johns","St. Lucie","Santa Rosa","Sarasota",
      "Seminole","Sumter","Suwannee","Taylor","Union","Volusia",
      "Wakulla","Walton","Washington")))

  .WX_CITIES_LIST <- c(
    "Jacksonville (KJAX)"    = "KJAX", "Tallahassee (KTLH)"     = "KTLH",
    "Gainesville (KGNV)"     = "KGNV", "Ocala (KOCF)"           = "KOCF",
    "Orlando (KMCO)"         = "KMCO", "Daytona Beach (KDAB)"   = "KDAB",
    "Tampa (KTPA)"           = "KTPA", "St. Petersburg (KSPG)"  = "KSPG",
    "Sarasota (KSRQ)"        = "KSRQ", "Fort Myers (KRSW)"      = "KRSW",
    "Miami (KMIA)"           = "KMIA", "Fort Lauderdale (KFLL)" = "KFLL",
    "West Palm Beach (KPBI)" = "KPBI", "Key West (KEYW)"        = "KEYW",
    "Pensacola (KPNS)"       = "KPNS", "Panama City (KECP)"     = "KECP")

  output$unified_rpt_params <- renderUI({
    tmpl <- input$unified_rpt_template %||% "alert_summary"
    switch(tmpl,
      alert_summary = fluidRow(
        column(4, selectInput("urpt_days", "Report Period",
          choices=c("1 day"=1,"7 days"=7,"14 days"=14,"30 days"=30), selected=7)),
        column(4, selectInput("urpt_zone", "Zone",
          choices=c("All Florida","North Florida","Central Florida","South Florida",
                    "Tampa","Miami","Orlando","Jacksonville","Gainesville"),
          selected="All Florida"))
      ),
      county_alerts = fluidRow(
        column(6, selectInput("urpt_county", "County",
          choices=.FL_COUNTIES_LIST, selected="All Florida"))
      ),
      weather_trends = fluidRow(
        column(4, selectInput("urpt_icao", "Station", choices=.WX_CITIES_LIST, selected="KGNV")),
        column(4, dateRangeInput("urpt_dates", "Date Range",
          start=Sys.Date()-30, end=Sys.Date(), min=Sys.Date()-90, max=Sys.Date()))
      ),
      traffic_analysis = fluidRow(
        column(4, selectInput("urpt_ta_county", "County",
          choices=.FL_COUNTIES_LIST[-1], selected="Alachua")),  # no "All Florida" for traffic
        column(4, dateInput("urpt_ta_date", "Date", value=Sys.Date()))
      ),
      census_impact = fluidRow(
        column(8, selectInput("urpt_alert_id", "Alert",
          choices=c("Loading..." = ""), selected=""))
      ),
      # BCP variants — all need user + asset
      fluidRow(
        column(4, selectInput("urpt_bcp_user", "User",
          choices=c("-- select a user --"=""), selected="")),
        column(4, selectInput("urpt_bcp_asset", "Asset / Property",
          choices=c("Select a user first"=""), selected=""))
      )
    )
  })

  # Populate census alert dropdown when census impact template is selected
  observeEvent(input$unified_rpt_template, {
    if (input$unified_rpt_template == "census_impact") {
      col <- tryCatch(mongo(collection="nws_alerts", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
      if (is.null(col)) return()
      tryCatch({
        alerts <- col$find('{}',
          fields='{"alert_id":1,"event":1,"area_desc":1,"_id":1}',
          sort='{"fetched_at":-1}', limit=50)
        col$disconnect()
        if (nrow(alerts) > 0) {
          labels <- paste0(alerts$event, " — ", substr(alerts$area_desc, 1, 45))
          ids    <- as.character(alerts$alert_id)
          updateSelectInput(session, "urpt_alert_id",
            choices=c("-- select alert --"="", setNames(ids, labels)))
        }
      }, error=function(e) tryCatch(col$disconnect(), error=function(e2) NULL))
    }
    # Populate BCP user list for BCP templates
    if (grepl("^bcp_", input$unified_rpt_template)) {
      col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
      if (is.null(col)) return()
      tryCatch({
        u <- col$find("{}", fields='{"username":1,"_id":0}')
        col$disconnect()
        unames  <- if (nrow(u) > 0) sort(u$username) else character(0)
        choices <- c("-- select a user --"="", setNames(unames, unames))
        updateSelectInput(session, "urpt_bcp_user", choices=choices)
      }, error=function(e) tryCatch(col$disconnect(), error=function(e2) NULL))
    }
  })

  # Populate BCP asset dropdown when user changes
  observeEvent(input$urpt_bcp_user, {
    uname <- input$urpt_bcp_user %||% ""
    if (nchar(uname) == 0) return()
    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) return()
    tryCatch({
      u      <- col$find(sprintf('{"username":"%s"}', uname), fields='{"assets":1,"_id":0}')
      col$disconnect()
      assets <- if (nrow(u) > 0 && !is.null(u$assets)) u$assets[[1]] else NULL
      if (is.null(assets) || (is.data.frame(assets) && nrow(assets) == 0)) {
        updateSelectInput(session, "urpt_bcp_asset", choices=c("No assets registered"=""))
        return()
      }
      if (is.data.frame(assets)) {
        choices <- setNames(as.character(assets$asset_id), assets$asset_name)
      } else {
        nms <- sapply(assets, function(a) a$asset_name %||% "Asset")
        ids <- sapply(assets, function(a) a$asset_id   %||% "")
        choices <- setNames(ids, nms)
      }
      updateSelectInput(session, "urpt_bcp_asset", choices=choices)
    }, error=function(e) tryCatch(col$disconnect(), error=function(e2) NULL))
  })

  # Unified generate button
  observeEvent(input$btn_unified_gen, {
    if (!isTRUE(auth_rv$role %in% c("operator","admin"))) {
      unified_rpt_status("Access denied."); return()
    }
    tmpl <- input$unified_rpt_template %||% "alert_summary"
    unified_rpt_status("Generating PDF\u2026 (this may take 30\u201360 s)")
    dir.create(rpt_output_dir, showWarnings=FALSE, recursive=TRUE)
    timestamp <- format(Sys.time(), "%Y%m%d_%H%M")
    do_email  <- isTRUE(input$unified_rpt_email)

    tryCatch({
      output_file <- switch(tmpl,
        alert_summary = {
          days  <- as.integer(input$urpt_days %||% 7)
          zone  <- input$urpt_zone %||% "All Florida"
          f <- file.path(rpt_output_dir, paste0("alert_summary_", gsub(" ","_",zone), "_", timestamp, ".pdf"))
          withr::with_dir(tempdir(), rmarkdown::render(
            "/home/ufuser/Fpren-main/reports/fpren_alert_report.Rmd",
            output_file=f, intermediates_dir=tempdir(),
            params=list(days_back=days, zone_label=zone, mongo_uri=MONGO_URI), quiet=TRUE))
          f
        },
        county_alerts = {
          county <- input$urpt_county %||% "All Florida"
          f <- file.path(rpt_output_dir, paste0("county_alerts_", gsub("[^A-Za-z0-9]","_",county), "_", timestamp, ".pdf"))
          withr::with_dir(tempdir(), rmarkdown::render(
            "/home/ufuser/Fpren-main/reports/county_alerts_report.Rmd",
            output_file=f, intermediates_dir=tempdir(),
            params=list(county_name=county, date=format(Sys.Date(),"%Y-%m-%d"), mongo_uri=MONGO_URI), quiet=TRUE))
          f
        },
        weather_trends = {
          icao      <- input$urpt_icao %||% "KGNV"
          city_name <- names(.WX_CITIES_LIST)[.WX_CITIES_LIST == icao]
          if (length(city_name)==0) city_name <- icao
          date_from <- as.character(input$urpt_dates[1] %||% (Sys.Date()-30))
          date_to   <- as.character(input$urpt_dates[2] %||% Sys.Date())
          f <- file.path(rpt_output_dir, paste0("weather_trends_", icao, "_", timestamp, ".pdf"))
          withr::with_dir(tempdir(), rmarkdown::render(
            "/home/ufuser/Fpren-main/reports/weather_trends_report.Rmd",
            output_file=f, intermediates_dir=tempdir(),
            params=list(icao=icao, city_name=city_name, date_from=date_from,
                        date_to=date_to, mongo_uri=MONGO_URI), quiet=TRUE))
          f
        },
        traffic_analysis = {
          county <- input$urpt_ta_county %||% "Alachua"
          date   <- as.character(input$urpt_ta_date %||% Sys.Date())
          f <- file.path(rpt_output_dir, paste0("traffic_", gsub("[^A-Za-z0-9]","_",county), "_", timestamp, ".pdf"))
          withr::with_dir(tempdir(), rmarkdown::render(
            "/home/ufuser/Fpren-main/reports/traffic_analysis_report.Rmd",
            output_file=f, intermediates_dir=tempdir(),
            params=list(county=county, report_date=date, mongo_uri=MONGO_URI), quiet=TRUE))
          f
        },
        census_impact = {
          alert_id <- input$urpt_alert_id %||% ""
          if (nchar(alert_id)==0) stop("Select an alert first.")
          r   <- httr::GET(paste0(CENSUS_API,"/impact/",URLencode(alert_id)), httr::timeout(30))
          dat <- httr::content(r, as="parsed", type="application/json")
          if (!isTRUE(dat$ok)) stop(dat$message %||% "Impact API error")
          alert <- dat$alert; imp <- alert$census_impact
          counties_json <- tryCatch(jsonlite::toJSON(imp$county_data%||%list(), auto_unbox=TRUE), error=function(e) "{}")
          f <- file.path(rpt_output_dir, paste0("census_impact_", gsub("[^A-Za-z0-9]","_", alert$event%||%"alert"), "_", timestamp, ".pdf"))
          withr::with_dir(tempdir(), rmarkdown::render(
            "/home/ufuser/Fpren-main/reports/census_impact_report.Rmd",
            output_file=f, intermediates_dir=tempdir(),
            params=list(alert_event=alert$event%||%"", alert_area=alert$area_desc%||%"",
                        alert_severity=alert$severity%||%"", alert_headline=alert$headline%||%"",
                        alert_description=alert$description%||%"",
                        total_population=imp$total_population_at_risk%||%0,
                        counties_json=as.character(counties_json),
                        ai_analysis=imp$ai_analysis%||%"", mongo_uri=MONGO_URI), quiet=TRUE))
          f
        },
        {
          # All BCP variants
          tmpl_map <- list(
            bcp_general      = "/home/ufuser/Fpren-main/reports/business_continuity_report.Rmd",
            bcp_broadcast    = "/home/ufuser/Fpren-main/reports/bcp_broadcast_facility.Rmd",
            bcp_county_em    = "/home/ufuser/Fpren-main/reports/bcp_county_em.Rmd",
            bcp_campus_police= "/home/ufuser/Fpren-main/reports/bcp_campus_police.Rmd"
          )
          rmd_file <- tmpl_map[[tmpl]]
          if (is.null(rmd_file)) stop("Unknown template.")
          uname    <- input$urpt_bcp_user  %||% ""
          asset_id <- input$urpt_bcp_asset %||% ""
          if (nchar(uname)==0 || nchar(asset_id)==0) stop("Select a user and asset.")
          col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
          if (is.null(col)) stop("DB unavailable.")
          u <- col$find(sprintf('{"username":"%s"}', uname), fields='{"assets":1,"_id":0}')
          col$disconnect()
          assets <- if (nrow(u)>0 && !is.null(u$assets)) u$assets[[1]] else NULL
          asset  <- if (is.data.frame(assets)) {
            row <- assets[assets$asset_id == asset_id, ]
            if (nrow(row)==0) stop("Asset not found.") else as.list(row[1,])
          } else {
            a <- Filter(function(x) x$asset_id==asset_id, assets)
            if (length(a)==0) stop("Asset not found.") else a[[1]]
          }
          f <- file.path(rpt_output_dir,
            paste0("bcp_", sub("bcp_","",tmpl), "_", uname, "_", timestamp, ".pdf"))
          withr::with_dir(tempdir(), rmarkdown::render(rmd_file,
            output_file=f, intermediates_dir=tempdir(),
            params=list(username=uname, asset_name=asset$asset_name%||%"",
                        address=asset$address%||%"", lat=asset$lat%||%0,
                        lon=asset$lon%||%0, zip=asset$zip%||%"",
                        city=asset$city%||%"",
                        nearest_airport_icao=asset$nearest_airport_icao%||%"KGNV",
                        nearest_airport_name=asset$nearest_airport_name%||%"",
                        asset_type=asset$asset_type%||%"", notes=asset$notes%||%"",
                        mongo_uri=MONGO_URI, days_back=7L), quiet=TRUE))
          f
        }
      )

      # Optionally email
      if (do_email) {
        sc        <- tryCatch(jsonlite::fromJSON("/home/ufuser/Fpren-main/weather_rss/config/smtp_config.json"), error=function(e) list())
        smtp_host <- sc$smtp_host %||% "smtp.ufl.edu"
        smtp_port <- as.integer(sc$smtp_port %||% 25)
        mail_from <- sc$mail_from %||% "lawrence.bornace@ufl.edu"
        mail_to   <- sc$mail_to   %||% "lawrence.bornace@ufl.edu"
        library(emayili)
        em <- envelope() %>% from(mail_from) %>% to(mail_to) %>%
          subject(paste0("FPREN Report — ", basename(output_file))) %>%
          text(paste0("FPREN automated report attached.\nGenerated: ", format(Sys.time(), "%Y-%m-%d %H:%M:%S UTC"))) %>%
          attachment(output_file)
        server(host=smtp_host, port=smtp_port, reuse=FALSE)(em, verbose=FALSE)
        unified_rpt_status(paste0("Saved & emailed: ", basename(output_file)))
      } else {
        unified_rpt_status(paste0("Saved: ", basename(output_file)))
      }
      showNotification(paste0("PDF saved: ", basename(output_file)), type="message")

    }, error=function(e) {
      unified_rpt_status(paste0("Error: ", conditionMessage(e)))
      showNotification(conditionMessage(e), type="error")
    })
  })

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
    if (!isTRUE(auth_rv$role %in% c("operator","admin"))) { rpt_status_msg("Access denied."); return() }
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

  # Store all user rows for profile card lookup
  users_data_rv <- reactiveVal(NULL)

  output$users_table <- DT::renderDataTable({
    user_mgmt_rv()
    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) return(data.frame(Message="DB unavailable"))
    tryCatch({
      u <- col$find("{}", fields='{"password":0,"verify_code":0,"reset_code":0,"invite_token":0,"_id":0}')
      col$disconnect()
      if (nrow(u) == 0) return(data.frame(Message="No users found"))
      users_data_rv(u)

      gf <- function(field) if (field %in% names(u)) {
        v <- u[[field]]
        if (is.list(v)) sapply(v, function(x) if (is.null(x)||is.na(x)) "" else as.character(x))
        else as.character(v)
      } else rep("", nrow(u))

      display <- data.frame(
        Username   = gf("username"),
        Name       = trimws(paste(gf("first_name"), gf("last_name"))),
        Title      = gf("title"),
        Profession = gf("profession"),
        Role       = gf("role"),
        Email      = gf("email"),
        Active     = ifelse(gf("active") %in% c("TRUE","true"), "Yes", "No"),
        `Last Login` = gf("last_login"),
        stringsAsFactors = FALSE, check.names = FALSE
      )
      datatable(display, selection="single", rownames=FALSE,
                options=list(pageLength=10, scrollX=TRUE))
    }, error=function(e) {
      tryCatch(col$disconnect(), error=function(e2) NULL)
      data.frame(Error=conditionMessage(e))
    })
  }, selection="single", rownames=FALSE, options=list(pageLength=10))

  # ── Profile card (appears when a row is selected) ────────────────────────────
  edit_user_target <- reactiveVal(NULL)

  .AVATAR_COLORS <- c("#003087","#1a6b3a","#884ea0","#e07020","#c0392b","#17a589","#1c2b6e")
  .avatar_color  <- function(uname) .AVATAR_COLORS[(utf8ToInt(substr(uname,1,1))[[1]] %% length(.AVATAR_COLORS)) + 1]
  .initials      <- function(first, last, uname) toupper(paste0(
    substr(if(nchar(first)>0) first else uname, 1, 1), substr(last, 1, 1)))

  output$user_profile_card <- renderUI({
    sel <- input$users_table_rows_selected
    u   <- users_data_rv()
    if (is.null(sel) || length(sel)==0 || is.null(u) || nrow(u)<sel) return(NULL)

    gf <- function(field) {
      if (!field %in% names(u)) return("")
      v <- u[[field]][sel]
      if (is.null(v) || is.na(v) || v == "NULL") "" else as.character(v)
    }

    uname  <- gf("username")
    first  <- gf("first_name"); last <- gf("last_name")
    pic    <- gf("profile_pic_url")
    bg     <- .avatar_color(uname)
    ini    <- .initials(first, last, uname)
    active_badge <- if (gf("active") %in% c("TRUE","true"))
      tags$span(class="label label-success", "Active") else
      tags$span(class="label label-danger", "Inactive")

    avatar_ui <- if (nchar(pic) > 4)
      tags$img(src=pic, style=sprintf("width:80px;height:80px;border-radius:50%%;object-fit:cover;border:3px solid %s;", bg))
    else
      tags$div(style=sprintf(
        "width:80px;height:80px;border-radius:50%%;background:%s;display:flex;align-items:center;justify-content:center;color:white;font-weight:bold;font-size:26px;", bg), ini)

    div(style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:6px;padding:16px;margin-bottom:12px;",
      fluidRow(
        column(2, div(style="text-align:center;", avatar_ui)),
        column(7,
          tags$h4(style="margin:0 0 2px;",
            if(nchar(trimws(paste(first,last)))>0) trimws(paste(first,last)) else uname),
          tags$p(style="margin:0;color:#555;font-size:13px;",
            if(nchar(gf("title"))>0) paste0(gf("title"), " — "), gf("department")),
          tags$p(style="margin:4px 0 0;font-size:13px;",
            icon("envelope"), " ", gf("email"), tags$span(style="margin:0 8px;","·"),
            icon("phone"), " ", gf("phone")),
          tags$p(style="margin:4px 0 0;font-size:12px;color:#666;",
            tags$b("Role:"), " ", gf("role"), "  ",
            tags$b("Profession:"), " ", gf("profession"), "  ",
            active_badge),
          if(nchar(gf("notes"))>0)
            tags$p(style="margin:6px 0 0;font-size:12px;font-style:italic;color:#777;", gf("notes"))
        ),
        column(3, style="text-align:right;padding-top:10px;",
          actionButton("btn_edit_user", tagList(icon("user-edit"), " Edit Profile"),
                       class="btn-info btn-sm"),
          br(), br(),
          actionButton("btn_delete_user", tagList(icon("user-minus"), " Delete"),
                       class="btn-danger btn-sm")
        )
      )
    )
  })

  # Open edit modal when Edit Profile is clicked
  observeEvent(input$btn_edit_user, {
    if (!isTRUE(auth_rv$role == "admin")) return()
    sel <- input$users_table_rows_selected
    u   <- users_data_rv()
    if (is.null(sel) || length(sel)==0 || is.null(u) || nrow(u)<sel) {
      showNotification("Select a user row first.", type="warning"); return()
    }

    gf <- function(field) {
      if (!field %in% names(u)) return("")
      v <- u[[field]][sel]
      if (is.null(v) || is.na(v) || identical(v, "NULL")) "" else as.character(v)
    }
    uname  <- gf("username")
    first  <- gf("first_name"); last <- gf("last_name")
    pic    <- gf("profile_pic_url")
    bg     <- .avatar_color(uname)
    ini    <- .initials(first, last, uname)

    avatar_ui <- if (nchar(pic) > 4)
      tags$img(src=pic, style=sprintf("width:80px;height:80px;border-radius:50%%;object-fit:cover;border:3px solid %s;display:block;margin:0 auto;", bg))
    else
      tags$div(style=sprintf(
        "width:80px;height:80px;border-radius:50%%;background:%s;display:flex;align-items:center;justify-content:center;color:white;font-weight:bold;font-size:26px;margin:0 auto;", bg), ini)

    edit_user_target(list(username=uname))

    showModal(modalDialog(
      title = tagList(icon("user-edit"), " Edit User Profile — ", tags$b(uname)),
      size  = "l", easyClose = FALSE,
      fluidRow(
        # Left column: avatar + account status
        column(3,
          div(style="text-align:center;padding:8px;",
            avatar_ui, br(),
            textInput("edit_pic_url", "Profile Picture URL",
              value=pic, placeholder="https://example.com/photo.jpg"),
            tags$small(style="color:#888;", "Paste any public image URL"),
            hr(),
            tags$b("Account"),
            checkboxInput("edit_active", "Account Active",
              value = gf("active") %in% c("TRUE","true")),
            tags$small(style="color:#666;display:block;line-height:1.8;",
              icon("envelope-open"), " Email: ",
              if(gf("email_verified") %in% c("TRUE","true")) "\u2705 verified" else "\u274c unverified", br(),
              icon("mobile-alt"), " Phone: ",
              if(gf("phone_verified") %in% c("TRUE","true")) "\u2705 verified" else "\u274c unverified", br(),
              icon("calendar"), " Created: ", substr(gf("created_at"),1,10)
            )
          )
        ),
        # Right column: all editable fields
        column(9,
          h5(style="color:#003087;border-bottom:1px solid #dee2e6;padding-bottom:6px;",
             icon("id-card"), " Personal Information"),
          fluidRow(
            column(6, textInput("edit_first_name", "First Name",
              value=first, placeholder="Jane")),
            column(6, textInput("edit_last_name", "Last Name",
              value=last, placeholder="Smith"))
          ),
          fluidRow(
            column(6, textInput("edit_title", "Job Title",
              value=gf("title"), placeholder="Chief Engineer")),
            column(6, textInput("edit_department", "Department / Organization",
              value=gf("department"), placeholder="WUFT Engineering"))
          ),
          h5(style="color:#003087;border-bottom:1px solid #dee2e6;padding-bottom:6px;margin-top:14px;",
             icon("address-book"), " Contact & Access"),
          fluidRow(
            column(6, textInput("edit_email", "Email",
              value=gf("email"), placeholder="user@ufl.edu")),
            column(6, textInput("edit_phone", "Phone",
              value=gf("phone"), placeholder="+13525551234"))
          ),
          fluidRow(
            column(6, selectInput("edit_role", "Dashboard Role",
              choices=c("admin","operator","viewer"), selected=gf("role"))),
            column(6, selectInput("edit_profession", "Profession",
              choices=c(
                list("-- Select --"=""),
                list(
                  "Broadcast"=c("Broadcast Engineer"="Broadcast Engineer",
                    "Broadcast Administrator"="Broadcast Administrator",
                    "Broadcast Operator"="Broadcast Operator",
                    "Program Director"="Program Director",
                    "Chief Engineer"="Chief Engineer",
                    "News Director"="News Director",
                    "Production Manager"="Production Manager"),
                  "Law Enforcement"=c("Police Chief"="Police Chief",
                    "Police Lieutenant"="Police Lieutenant",
                    "Police Officer"="Police Officer",
                    "Campus Security Officer"="Campus Security Officer",
                    "Dispatch Coordinator"="Dispatch Coordinator",
                    "Emergency Services Coordinator"="Emergency Services Coordinator"),
                  "Emergency Management"=c("County Emergency Manager"="County Emergency Manager",
                    "City Administrator"="City Administrator",
                    "Public Safety Director"="Public Safety Director",
                    "EOC Coordinator"="EOC Coordinator",
                    "FEMA Liaison"="FEMA Liaison",
                    "Hazmat Coordinator"="Hazmat Coordinator"),
                  "General"=c("Facility Manager"="Facility Manager",
                    "IT/Systems Administrator"="IT/Systems Administrator",
                    "Station Manager"="Station Manager","Other"="Other")
                )
              ), selected=gf("profession")))
          ),
          h5(style="color:#003087;border-bottom:1px solid #dee2e6;padding-bottom:6px;margin-top:14px;",
             icon("sticky-note"), " Notes"),
          textAreaInput("edit_notes", NULL, value=gf("notes"), rows=3,
            placeholder="Any additional notes about this user\u2026", width="100%")
        )
      ),
      footer = tagList(
        actionButton("btn_save_user_profile", "Save Profile",
                     class="btn-success btn-lg", icon=icon("save")),
        modalButton("Cancel")
      )
    ))
  })

  # Save profile to MongoDB
  observeEvent(input$btn_save_user_profile, {
    target <- edit_user_target()
    if (is.null(target)) return()
    uname <- target$username %||% ""
    if (nchar(uname)==0) return()

    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) { showNotification("DB unavailable.", type="error"); return() }
    tryCatch({
      update_doc <- list(
        `$set` = list(
          first_name      = input$edit_first_name  %||% "",
          last_name       = input$edit_last_name   %||% "",
          title           = input$edit_title       %||% "",
          department      = input$edit_department  %||% "",
          email           = input$edit_email       %||% "",
          phone           = input$edit_phone       %||% "",
          role            = input$edit_role        %||% "viewer",
          profession      = input$edit_profession  %||% "",
          active          = isTRUE(input$edit_active),
          profile_pic_url = input$edit_pic_url     %||% "",
          notes           = input$edit_notes       %||% ""
        )
      )
      col$update(sprintf('{"username":"%s"}', uname),
                 jsonlite::toJSON(update_doc, auto_unbox=TRUE))
      col$disconnect()
      log_audit("user_edit", uname, auth_rv$username %||% "admin",
                paste("Profile updated for", uname))
      removeModal()
      user_mgmt_rv(user_mgmt_rv() + 1)
      showNotification(paste("Profile saved for", uname), type="message")
    }, error=function(e) {
      tryCatch(col$disconnect(), error=function(e2) NULL)
      showNotification(paste("Save error:", conditionMessage(e)), type="error")
    })
  })

  observeEvent(input$btn_add_user, {
    if (!isTRUE(auth_rv$role == "admin")) {
      user_mgmt_msg("Admin role required."); return()
    }
    email      <- trimws(input$new_user_email)
    phone      <- trimws(input$new_user_phone)
    role       <- input$new_user_role
    profession <- input$new_user_profession %||% ""
    if (nchar(email) == 0) { user_mgmt_msg("Email is required."); return() }

    # Derive username from email prefix
    uname <- tolower(gsub("[^a-z0-9._]", "", strsplit(email, "@")[[1]][1]))
    if (nchar(uname) == 0) uname <- paste0("user", format(Sys.time(), "%Y%m%d%H%M%S"))

    # Check if inviting admin has group invites disabled
    admin_invites_on <- TRUE
    creator <- auth_rv$username %||% "admin"
    if (creator != "admin") {
      col_c <- tryCatch(mongo("users", "weather_rss", url = MONGO_URI),
                        error = function(e) NULL)
      if (!is.null(col_c)) {
        cr <- tryCatch({
          r <- col_c$find(sprintf('{"username":"%s"}', creator),
                          fields = '{"group_invites_enabled":1,"_id":0}')
          col_c$disconnect()
          if (nrow(r) > 0) r[1, ] else NULL
        }, error = function(e) {
          tryCatch(col_c$disconnect(), error = function(e2) NULL); NULL
        })
        if (!is.null(cr) && isFALSE(cr$group_invites_enabled))
          admin_invites_on <- FALSE
      }
    }

    temp_pw      <- paste0(sample(c(letters, LETTERS, 0:9), 8, replace=TRUE), collapse="")
    pw_hash      <- bcrypt::hashpw(temp_pw)
    invite_tok   <- gen_token()
    now_str      <- format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz="UTC")
    inv_exp_str  <- format(Sys.time() + 3*24*3600, "%Y-%m-%dT%H:%M:%SZ", tz="UTC")
    invite_link  <- paste0(DASHBOARD_URL, "/?invite=", invite_tok)

    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) { user_mgmt_msg("DB unavailable."); return() }
    tryCatch({
      col$insert(list(
        username              = uname,
        email                 = email,
        phone                 = phone,
        password              = pw_hash,
        role                  = role,
        profession            = profession,
        group_invites_enabled = TRUE,
        active                = TRUE,
        email_verified        = FALSE,
        phone_verified        = FALSE,
        must_change_password  = TRUE,
        failed_attempts       = 0L,
        locked_until          = NULL,
        last_login            = NULL,
        created_at            = now_str,
        created_by            = creator,
        invite_token          = invite_tok,
        invite_expires        = inv_exp_str,
        verify_code           = NULL,
        verify_expires        = NULL,
        reset_code            = NULL,
        reset_expires         = NULL
      ))
      col$disconnect()
    }, error=function(e) {
      tryCatch(col$disconnect(), error=function(e2) NULL)
      user_mgmt_msg(paste("Error creating user:", conditionMessage(e)))
      return()
    })

    # Send invite email (with direct invite link + 72-hour expiry warning)
    if (admin_invites_on) {
      send_fpren_email(email,
        paste0("[FPREN] You have been invited to the FPREN Dashboard — action required"),
        paste0(
          "<div style='background:#003087;color:white;padding:20px 24px;border-radius:6px 6px 0 0;'>",
          "<h2 style='margin:0;font-size:22px;'>FPREN Dashboard Invitation</h2>",
          "<p style='margin:4px 0 0;opacity:0.85;font-size:14px;'>Florida Public Radio Emergency Network \u2014 University of Florida</p></div>",
          "<div style='background:#f9f9f9;padding:20px 24px;border:1px solid #ddd;border-top:none;border-radius:0 0 6px 6px;'>",
          "<p>Hello,</p>",
          "<p>You have been invited to access the <strong>FPREN Dashboard</strong>. ",
          "Click the button below to activate your account and set your password:</p>",
          "<div style='text-align:center;margin:20px 0;'>",
          "<a href='", invite_link, "' style='background:#003087;color:white;",
          "padding:12px 28px;border-radius:6px;text-decoration:none;font-size:16px;font-weight:bold;'>",
          "Activate My Account &rarr;</a></div>",
          "<p style='font-size:12px;color:#666;word-break:break-all;'>Or paste this link: <code>",
          invite_link, "</code></p>",
          "<div style='background:#fce4e4;border:2px solid #e74c3c;border-radius:4px;padding:12px 16px;margin:16px 0;'>",
          "<strong style='color:#c0392b;'>&#9888; This invitation expires in 72 hours (by ",
          format(Sys.time() + 3*24*3600, "%B %d, %Y at %I:%M %p", tz="America/New_York"), " ET).</strong><br>",
          "If you do not activate your account within 72 hours, your account will be <strong>automatically deleted</strong> ",
          "and you will need to request a new invitation.",
          "</div>",
          "<table style='border-collapse:collapse;margin:12px 0;background:#fff;border:1px solid #ddd;width:100%;font-size:13px;'>",
          "<tr><td style='padding:7px 12px;border-bottom:1px solid #eee;font-weight:bold;width:140px;'>Username</td>",
          "<td style='padding:7px 12px;border-bottom:1px solid #eee;font-family:monospace;'>", uname, "</td></tr>",
          "<tr><td style='padding:7px 12px;border-bottom:1px solid #eee;font-weight:bold;'>Role</td>",
          "<td style='padding:7px 12px;border-bottom:1px solid #eee;'>", role, "</td></tr>",
          if (nchar(profession) > 0) paste0(
            "<tr><td style='padding:7px 12px;font-weight:bold;'>Profession</td>",
            "<td style='padding:7px 12px;'>", profession, "</td></tr>") else "",
          "</table>",
          "<p style='font-size:12px;color:#666;'>After activation you will complete SMS and email verification. ",
          "Your account will be automatically disabled after <strong>6 months of inactivity</strong>.</p>",
          "<p style='font-size:12px;'>Questions? Contact <a href='mailto:lawrence.bornace@ufl.edu'>lawrence.bornace@ufl.edu</a>.</p>",
          "</div>"
        ))

      # Send SMS invite if phone provided
      if (nchar(phone) > 0) {
        sms_body <- paste0(
          "FPREN DASHBOARD INVITE\n",
          "You have been invited by ", creator, ".\n",
          "Username: ", uname, "\n",
          "Activate: ", invite_link, "\n",
          "EXPIRES IN 72 HRS. If not activated your account is deleted.\n",
          "Reply STOP to opt out. \u2014FPREN"
        )
        send_twilio_sms(phone, sms_body)
      }
    }

    # Log and notify
    log_audit("user_add", uname, creator,
              paste0("Added user ", uname, " (", email, ") role:", role,
                     if (!admin_invites_on) " [invites suppressed by creator]" else ""))

    send_notification_emails(
      paste("FPREN: New user added:", uname),
      paste0("<h3>FPREN User Management Notification</h3>",
             "<p><strong>Action:</strong> New user added</p>",
             "<p><strong>Username:</strong> ", uname, "</p>",
             "<p><strong>Email:</strong> ", email, "</p>",
             "<p><strong>Role:</strong> ", role, "</p>",
             "<p><strong>Invite sent:</strong> ", if (admin_invites_on) "Yes" else "No (suppressed by creator setting)", "</p>",
             "<p><strong>Invite expires:</strong> ", inv_exp_str, "</p>",
             "<p><strong>Performed by:</strong> ", creator, "</p>",
             "<p><strong>Date/Time:</strong> ", now_str, "</p>")
    )

    user_mgmt_msg(paste0(
      "User ", uname, " created",
      if (admin_invites_on)
        paste0(" — invite email sent to ", email,
               if (nchar(phone) > 0) paste0(" + SMS to ", phone) else "",
               " (expires 72 hours)")
      else
        " — invite suppressed (your group_invites_enabled is OFF)"
    ))
    updateTextInput(session, "new_user_email", value="")
    updateTextInput(session, "new_user_phone", value="")
    updateSelectInput(session, "new_user_profession", selected="")
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

  # ── User Assets Management (Admin Only) ─────────────────────────────────────
  asset_mgmt_msg <- reactiveVal("")
  asset_mgmt_rv  <- reactiveVal(0)

  output$asset_mgmt_status <- renderText({ asset_mgmt_msg() })

  # Populate the user dropdowns for both asset management and BCP generation
  observe({
    user_mgmt_rv()  # refresh when users change
    if (!isTRUE(auth_rv$role == "admin")) return()
    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) return()
    tryCatch({
      u <- col$find("{}", fields='{"username":1,"_id":0}')
      col$disconnect()
      unames <- if (nrow(u) > 0) sort(u$username) else character(0)
      choices <- c("-- select a user --" = "", setNames(unames, unames))
      updateSelectInput(session, "asset_mgmt_user", choices = choices)
      updateSelectInput(session, "bcp_username",    choices = choices)
    }, error=function(e) { tryCatch(col$disconnect(), error=function(e2) NULL) })
  })

  # Load assets table when user is selected or refresh triggered
  # Helper: load assets for a user from MongoDB, sorted by priority
  .load_user_assets <- function(uname) {
    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) return(NULL)
    tryCatch({
      u <- col$find(sprintf('{"username":"%s"}', uname), fields='{"assets":1,"_id":0}')
      col$disconnect()
      if (nrow(u) == 0 || is.null(u$assets) || length(u$assets[[1]]) == 0) return(data.frame())
      assets <- u$assets[[1]]
      if (!is.data.frame(assets)) assets <- as.data.frame(do.call(rbind, lapply(assets, as.data.frame)))
      # Ensure priority column exists; default to row order
      if (!"priority" %in% names(assets))
        assets$priority <- seq_len(nrow(assets))
      else
        assets$priority <- as.integer(assets$priority)
      assets[order(assets$priority), ]
    }, error=function(e) {
      tryCatch(col$disconnect(), error=function(e2) NULL)
      NULL
    })
  }

  # Helper: save full assets array back to MongoDB (used after reordering)
  .save_user_assets <- function(uname, assets_df) {
    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) return(FALSE)
    tryCatch({
      assets_list <- lapply(seq_len(nrow(assets_df)), function(i) as.list(assets_df[i, ]))
      col$update(
        sprintf('{"username":"%s"}', uname),
        sprintf('{"$set":{"assets":%s}}', jsonlite::toJSON(assets_list, auto_unbox=TRUE))
      )
      col$disconnect()
      TRUE
    }, error=function(e) {
      tryCatch(col$disconnect(), error=function(e2) NULL)
      FALSE
    })
  }

  output$user_assets_table <- DT::renderDataTable({
    asset_mgmt_rv()
    uname <- input$asset_mgmt_user
    if (is.null(uname) || nchar(uname) == 0)
      return(data.frame(Message="Select a user above to view their assets"))
    assets <- .load_user_assets(uname)
    if (is.null(assets) || nrow(assets) == 0)
      return(data.frame(Message="No assets registered for this user"))
    keep <- intersect(c("priority","asset_name","asset_type","city","address",
                        "nearest_airport_icao","notes","asset_id"), names(assets))
    df <- assets[, keep, drop=FALSE]
    # Rename priority column for display
    names(df)[names(df)=="priority"] <- "Priority"
    names(df)[names(df)=="asset_name"] <- "Name"
    names(df)[names(df)=="asset_type"] <- "Type"
    names(df)[names(df)=="city"] <- "City"
    DT::datatable(df, selection="single",
                  options=list(pageLength=10, scrollX=TRUE, order=list(list(0,"asc"))),
                  rownames=FALSE)
  })

  # Shared geocode helper — calls /api/lookup/geocode with address + zip
  .do_geocode <- function(address, zip) {
    params <- list()
    if (nchar(trimws(address)) > 0) params[["address"]] <- trimws(address)
    if (nchar(trimws(zip))     > 0) params[["zip"]]     <- trimws(zip)
    if (length(params) == 0) return(list(ok=FALSE, message="No input"))
    tryCatch({
      r <- httr::GET("http://localhost:5000/api/lookup/geocode",
                     query = params, httr::timeout(15))
      httr::content(r, as="parsed", type="application/json")
    }, error=function(e) list(ok=FALSE, message=conditionMessage(e)))
  }

  .apply_geocode_result <- function(result) {
    if (!isTRUE(result$ok)) {
      asset_mgmt_msg(paste0("Lookup failed: ", result$message %||% "unknown error"))
      return()
    }
    updateNumericInput(session, "new_asset_lat", value = result$lat)
    updateNumericInput(session, "new_asset_lon", value = result$lon)
    updateSelectInput(session, "new_asset_city_sel",
      choices = c(result$city), selected = result$city)
    updateSelectInput(session, "new_asset_airport_sel",
      choices  = c(paste0(result$nearest_airport_icao, " \u2014 ", result$nearest_airport_name)),
      selected = paste0(result$nearest_airport_icao, " \u2014 ", result$nearest_airport_name))
    src_label <- if (identical(result$source, "nominatim")) "address geocode" else "ZIP centroid"
    asset_mgmt_msg(paste0(
      "\u2713 Auto-filled from ", src_label, ": ",
      result$county, " county \u2192 ", result$city,
      " (", result$lat, ", ", result$lon, ") / ", result$nearest_airport_icao
    ))
  }

  # Auto-populate when ZIP reaches 5 digits (with or without address)
  addr_zip <- reactive({
    list(address = input$new_asset_address %||% "",
         zip     = input$new_asset_zip     %||% "")
  })
  addr_zip_d <- debounce(addr_zip, 900)  # wait 900 ms after last keystroke

  observe({
    vals <- addr_zip_d()
    zip  <- trimws(vals$zip)
    addr <- trimws(vals$address)
    # Only fire when ZIP is complete (5 digits)
    if (!grepl("^\\d{5}$", zip)) return()
    .apply_geocode_result(.do_geocode(addr, zip))
  })

  # Manual button still works as a fallback
  observeEvent(input$btn_lookup_zip, {
    result <- .do_geocode(
      input$new_asset_address %||% "",
      input$new_asset_zip     %||% ""
    )
    .apply_geocode_result(result)
  })

  output$new_asset_city_ui <- renderUI({
    selectInput("new_asset_city_sel", "City (nearest)", choices=character(0))
  })
  output$new_asset_airport_ui <- renderUI({
    selectInput("new_asset_airport_sel", "Nearest Airport", choices=character(0))
  })

  # Add asset — writes directly to MongoDB, fetches nearby resources via public API
  observeEvent(input$btn_add_asset, {
    if (!isTRUE(auth_rv$role == "admin")) { asset_mgmt_msg("Admin required."); return() }
    uname <- input$asset_mgmt_user
    if (is.null(uname) || nchar(uname) == 0) { asset_mgmt_msg("Select a user first."); return() }
    aname <- trimws(input$new_asset_name)
    if (nchar(aname) == 0) { asset_mgmt_msg("Asset name is required."); return() }

    apt_sel  <- input$new_asset_airport_sel %||% ""
    apt_icao <- trimws(strsplit(apt_sel, " ")[[1]][1])
    apt_name <- if (nchar(apt_sel) > 6) trimws(sub("^\\S+\\s+[—-]+\\s*", "", apt_sel)) else apt_sel

    lat <- as.numeric(input$new_asset_lat %||% 0)
    lon <- as.numeric(input$new_asset_lon %||% 0)
    if (is.na(lat)) lat <- 0
    if (is.na(lon)) lon <- 0

    asset_mgmt_msg("Adding asset and fetching nearby emergency resources (may take ~10 s)...")

    # Fetch nearby resources via the public (no-auth) Flask endpoint
    nearby <- list(fire_stations = list(), police = list(), hospitals = list(), supermarkets = list())
    if (lat != 0 && lon != 0) {
      nearby <- tryCatch({
        r <- httr::GET(
          sprintf("http://localhost:5000/api/lookup/nearby-resources?lat=%s&lon=%s&radius_m=5000",
                  lat, lon),
          httr::timeout(35)
        )
        d <- httr::content(r, as = "parsed", type = "application/json")
        if (isTRUE(d$ok)) {
          list(
            fire_stations = d$fire_stations %||% list(),
            police        = d$police        %||% list(),
            hospitals     = d$hospitals     %||% list(),
            supermarkets  = d$supermarkets  %||% list()
          )
        } else nearby
      }, error = function(e) nearby)
    }

    # Priority = one after last existing asset
    existing <- .load_user_assets(uname)
    next_priority <- if (!is.null(existing) && nrow(existing) > 0) max(existing$priority, na.rm=TRUE) + 1L else 1L

    new_asset <- list(
      asset_id             = paste0(sample(c(letters, LETTERS, 0:9), 16, replace=TRUE), collapse=""),
      asset_name           = aname,
      address              = trimws(input$new_asset_address %||% ""),
      lat                  = lat,
      lon                  = lon,
      zip                  = trimws(input$new_asset_zip %||% ""),
      city                 = trimws(input$new_asset_city_sel %||% ""),
      nearest_airport_icao = apt_icao,
      nearest_airport_name = apt_name,
      asset_type           = input$new_asset_type %||% "Facility",
      notes                = trimws(input$new_asset_notes %||% ""),
      priority             = next_priority,
      nearby_fire_stations = nearby$fire_stations,
      nearby_police        = nearby$police,
      nearby_hospitals     = nearby$hospitals,
      nearby_supermarkets  = nearby$supermarkets,
      created_at           = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz="UTC")
    )

    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) { asset_mgmt_msg("DB unavailable."); return() }
    tryCatch({
      col$update(
        sprintf('{"username":"%s"}', uname),
        sprintf('{"$push":{"assets":%s}}', jsonlite::toJSON(new_asset, auto_unbox=TRUE))
      )
      col$disconnect()
      n_fire   <- length(nearby$fire_stations)
      n_police <- length(nearby$police)
      n_hosp   <- length(nearby$hospitals)
      n_mkt    <- length(nearby$supermarkets)
      asset_mgmt_msg(paste0(
        "Asset '", aname, "' added to ", uname, ". ",
        "Nearby: ", n_fire, " fire station(s), ",
        n_police, " police, ",
        n_hosp, " hospital(s), ", n_mkt, " supermarket(s)."
      ))
      asset_mgmt_rv(asset_mgmt_rv() + 1)
    }, error = function(e) {
      tryCatch(col$disconnect(), error=function(e2) NULL)
      asset_mgmt_msg(paste("Error adding asset:", conditionMessage(e)))
    })
  })

  # Load assets button
  observeEvent(input$btn_load_user_assets, { asset_mgmt_rv(asset_mgmt_rv() + 1) })

  # Delete selected asset
  observeEvent(input$btn_delete_asset, {
    if (!isTRUE(auth_rv$role == "admin")) return()
    uname <- input$asset_mgmt_user
    sel   <- input$user_assets_table_rows_selected
    if (is.null(uname) || nchar(uname) == 0 || is.null(sel) || length(sel) == 0) {
      asset_mgmt_msg("Select a user and an asset row first."); return()
    }
    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) { asset_mgmt_msg("DB unavailable."); return() }
    tryCatch({
      u <- col$find(sprintf('{"username":"%s"}', uname), fields='{"assets":1,"_id":0}')
      assets <- if (nrow(u) > 0 && !is.null(u$assets)) u$assets[[1]] else NULL
      if (is.null(assets) || (is.data.frame(assets) && nrow(assets) < sel)) {
        col$disconnect(); asset_mgmt_msg("Asset not found."); return()
      }
      asset_id <- if (is.data.frame(assets)) as.character(assets$asset_id[sel])
                  else as.character(assets[[sel]]$asset_id)
      col$update(
        sprintf('{"username":"%s"}', uname),
        sprintf('{"$pull":{"assets":{"asset_id":"%s"}}}', asset_id)
      )
      col$disconnect()
      asset_mgmt_msg("Asset removed.")
      asset_mgmt_rv(asset_mgmt_rv() + 1)
    }, error=function(e) {
      tryCatch(col$disconnect(), error=function(e2) NULL)
      asset_mgmt_msg(paste("Error:", conditionMessage(e)))
    })
  })

  # Move asset up/down in priority order
  .move_asset <- function(uname, sel_row, direction) {
    if (is.null(uname) || nchar(uname) == 0 || is.null(sel_row) || length(sel_row) == 0) {
      asset_mgmt_msg("Select a user and an asset row first."); return()
    }
    assets <- .load_user_assets(uname)  # already sorted by priority
    if (is.null(assets) || nrow(assets) < 2) { asset_mgmt_msg("Nothing to reorder."); return() }
    n <- nrow(assets)
    swap_with <- sel_row + direction
    if (swap_with < 1 || swap_with > n) { asset_mgmt_msg("Already at the top/bottom."); return() }
    # Swap priorities
    tmp <- assets$priority[sel_row]
    assets$priority[sel_row]    <- assets$priority[swap_with]
    assets$priority[swap_with]  <- tmp
    # Re-sort and normalise to 1..n
    assets <- assets[order(assets$priority), ]
    assets$priority <- seq_len(n)
    if (.save_user_assets(uname, assets)) {
      asset_mgmt_rv(asset_mgmt_rv() + 1)
      asset_mgmt_msg(paste0("Asset priority updated."))
    } else {
      asset_mgmt_msg("DB error saving priority.")
    }
  }

  observeEvent(input$btn_asset_move_up, {
    if (!isTRUE(auth_rv$role == "admin")) return()
    .move_asset(input$asset_mgmt_user, input$user_assets_table_rows_selected, -1L)
  })

  observeEvent(input$btn_asset_move_down, {
    if (!isTRUE(auth_rv$role == "admin")) return()
    .move_asset(input$asset_mgmt_user, input$user_assets_table_rows_selected, +1L)
  })

  # Nearby resources panel — shown when a row is selected in the assets table
  output$asset_nearby_panel <- renderUI({
    sel   <- input$user_assets_table_rows_selected
    uname <- input$asset_mgmt_user
    asset_mgmt_rv()
    if (is.null(sel) || length(sel) == 0 || is.null(uname) || nchar(uname) == 0)
      return(NULL)

    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) return(NULL)
    asset <- tryCatch({
      u <- col$find(sprintf('{"username":"%s"}', uname), fields='{"assets":1,"_id":0}')
      col$disconnect()
      if (nrow(u) == 0 || is.null(u$assets)) return(NULL)
      assets <- u$assets[[1]]
      if (is.data.frame(assets) && nrow(assets) >= sel) as.list(assets[sel, ]) else NULL
    }, error=function(e) { tryCatch(col$disconnect(), error=function(e2) NULL); NULL })

    if (is.null(asset)) return(NULL)

    fire   <- asset$nearby_fire_stations %||% list()
    police <- asset$nearby_police        %||% list()
    hosp   <- asset$nearby_hospitals     %||% list()
    mkts   <- asset$nearby_supermarkets  %||% list()

    # mongolite may return data frames instead of lists — normalise
    .to_list <- function(x) {
      if (is.data.frame(x) && nrow(x) > 0) lapply(seq_len(nrow(x)), function(i) as.list(x[i,]))
      else if (is.list(x)) x else list()
    }
    fire <- .to_list(fire); police <- .to_list(police)
    hosp <- .to_list(hosp); mkts   <- .to_list(mkts)

    if (length(fire) == 0 && length(police) == 0 && length(hosp) == 0 && length(mkts) == 0) {
      return(div(style="margin-top:8px;",
        tags$div(class="alert alert-info", style="font-size:13px;",
          icon("info-circle"),
          tags$strong(" No nearby resources found."),
          tags$br(),
          tags$small("Resources are fetched automatically from OpenStreetMap when an asset is saved with valid coordinates. ",
                     "If missing, try deleting and re-adding the asset, or the OSM data may not be complete for this location.")
        )
      ))
    }

    .resource_card <- function(r, icon_name, color) {
      phone   <- r$phone   %||% ""
      address <- r$address %||% ""
      dist_km <- r$dist_km %||% NA
      src     <- r$source  %||% ""
      # source badge label
      src_label <- if (grepl("NPI", src)) "NPI"
                   else if (grepl("Nominatim", src)) "Geocoded"
                   else ""
      tags$li(
        style = paste0("margin-bottom:10px; list-style:none; border-left:3px solid ",
                       color, "; padding-left:8px;"),
        tags$div(
          tags$span(style=paste0("color:", color, ";"), icon(icon_name), " "),
          tags$strong(r$name %||% "Unknown"),
          if (!is.na(dist_km)) tags$span(
            style="color:#888; font-size:11px; margin-left:6px;",
            paste0("(", dist_km, " km)")
          ),
          if (nchar(src_label) > 0) tags$span(
            style="background:#e8f0ff; color:#3358a0; font-size:10px; border-radius:3px; padding:1px 4px; margin-left:5px;",
            src_label
          )
        ),
        if (nchar(address) > 0)
          tags$div(style="font-size:12px; color:#555; margin-top:2px;",
            icon("location-dot", style="font-size:10px;"), " ", address)
        else
          tags$div(style="font-size:11px; color:#aaa; font-style:italic; margin-top:2px;",
            "Address not available — verify manually"),
        if (nchar(phone) > 0)
          tags$div(style="font-size:12px; color:#1a6bb5; margin-top:2px;",
            icon("phone", style="font-size:10px;"), " ",
            tags$a(href=paste0("tel:", phone), phone, style="color:#1a6bb5;"))
        else
          tags$div(style="font-size:11px; color:#aaa; font-style:italic; margin-top:2px;",
            "Phone not available — verify manually")
      )
    }

    tagList(
      hr(),
      h5(icon("map-marker-alt"), paste0(" Nearby Resources — ", asset$asset_name %||% "")),
      tags$p(tags$small(style="color:#666;",
        icon("database"),
        " Sources: OpenStreetMap (location), Nominatim (addresses), CMS NPI Registry (hospital phones). 5 km radius. ",
        tags$span(style="background:#e8f0ff;color:#3358a0;border-radius:3px;padding:1px 4px;", "NPI"),
        " = federal healthcare registry. ",
        tags$span(style="background:#e8f0ff;color:#3358a0;border-radius:3px;padding:1px 4px;", "Geocoded"),
        " = address looked up from coordinates. Verify before official use."
      )),
      fluidRow(
        column(3,
          tags$h6(style="color:#c0392b;",
            icon("fire"), tags$strong(paste0(" Fire (", length(fire), ")"))),
          if (length(fire) == 0)
            tags$p(tags$small(style="color:#999;", "None found within 5 km"))
          else
            tags$ul(style="padding-left:4px;",
              lapply(fire[seq_len(min(3, length(fire)))],
                     .resource_card, icon_name="fire", color="#c0392b"))
        ),
        column(3,
          tags$h6(style="color:#2c3e7a;",
            icon("shield-halved"), tags$strong(paste0(" Police (", length(police), ")"))),
          if (length(police) == 0)
            tags$p(tags$small(style="color:#999;", "None found within 10 km"))
          else
            tags$ul(style="padding-left:4px;",
              lapply(police[seq_len(min(3, length(police)))],
                     .resource_card, icon_name="shield-halved", color="#2c3e7a"))
        ),
        column(3,
          tags$h6(style="color:#2980b9;",
            icon("hospital"), tags$strong(paste0(" Medical (", length(hosp), ")"))),
          if (length(hosp) == 0)
            tags$p(tags$small(style="color:#999;", "None found within 5 km"))
          else
            tags$ul(style="padding-left:4px;",
              lapply(hosp[seq_len(min(3, length(hosp)))],
                     .resource_card, icon_name="plus-square", color="#2980b9"))
        ),
        column(3,
          tags$h6(style="color:#27ae60;",
            icon("basket-shopping"), tags$strong(paste0(" Grocery / Supply (", length(mkts), ")"))),
          if (length(mkts) == 0)
            tags$p(tags$small(style="color:#999;", "None found within 5 km"))
          else
            tags$ul(style="padding-left:4px;",
              lapply(mkts[seq_len(min(3, length(mkts)))],
                     .resource_card, icon_name="basket-shopping", color="#27ae60"))
        )
      )
    )
  })

  # Refresh nearby resources for selected asset
  nearby_refresh_rv <- reactiveVal("")
  output$nearby_refresh_status <- renderText({ nearby_refresh_rv() })

  observeEvent(input$btn_refresh_nearby, {
    if (!isTRUE(auth_rv$role == "admin")) { nearby_refresh_rv("Admin required."); return() }
    uname <- input$asset_mgmt_user
    sel   <- input$user_assets_table_rows_selected
    if (is.null(uname) || nchar(uname) == 0 || is.null(sel) || length(sel) == 0) {
      nearby_refresh_rv("Select a user and asset row first.")
      return()
    }
    nearby_refresh_rv("Fetching nearby resources (may take ~10 s)...")

    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) { nearby_refresh_rv("DB unavailable."); return() }
    asset_id <- tryCatch({
      u <- col$find(sprintf('{"username":"%s"}', uname), fields='{"assets":1,"_id":0}')
      if (nrow(u) == 0 || is.null(u$assets)) { col$disconnect(); return() }
      assets <- u$assets[[1]]
      if (is.data.frame(assets) && nrow(assets) >= sel)
        as.list(assets[sel, ])$asset_id else NULL
    }, error=function(e) { tryCatch(col$disconnect(), error=function(e2) NULL); NULL })
    tryCatch(col$disconnect(), error=function(e2) NULL)
    if (is.null(asset_id)) { nearby_refresh_rv("Could not identify selected asset."); return() }

    # Get lat/lon of the asset
    col2 <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col2)) { nearby_refresh_rv("DB unavailable."); return() }
    asset <- tryCatch({
      u <- col2$find(sprintf('{"username":"%s"}', uname), fields='{"assets":1,"_id":0}')
      col2$disconnect()
      if (nrow(u) == 0 || is.null(u$assets)) NULL
      else {
        assets <- u$assets[[1]]
        if (is.data.frame(assets)) {
          idx <- which(assets$asset_id == asset_id)
          if (length(idx) > 0) as.list(assets[idx[1], ]) else NULL
        } else NULL
      }
    }, error=function(e) { tryCatch(col2$disconnect(), error=function(e2) NULL); NULL })
    if (is.null(asset)) { nearby_refresh_rv("Asset not found in database."); return() }

    lat <- as.numeric(asset$lat %||% 0)
    lon <- as.numeric(asset$lon %||% 0)
    if (is.na(lat) || is.na(lon) || lat == 0 || lon == 0) {
      nearby_refresh_rv("Asset has no valid coordinates.")
      return()
    }

    nearby <- tryCatch({
      r <- httr::GET(
        sprintf("http://localhost:5000/api/lookup/nearby-resources?lat=%s&lon=%s&radius_m=5000", lat, lon),
        httr::timeout(35)
      )
      d <- httr::content(r, as="parsed", type="application/json")
      if (isTRUE(d$ok)) list(
        fire_stations = d$fire_stations %||% list(),
        police        = d$police        %||% list(),
        hospitals     = d$hospitals     %||% list(),
        supermarkets  = d$supermarkets  %||% list()
      ) else list(fire_stations=list(), police=list(), hospitals=list(), supermarkets=list())
    }, error=function(e) list(fire_stations=list(), police=list(), hospitals=list(), supermarkets=list()))

    col3 <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col3)) { nearby_refresh_rv("DB unavailable."); return() }
    tryCatch({
      col3$update(
        sprintf('{"username":"%s","assets.asset_id":"%s"}', uname, asset_id),
        jsonlite::toJSON(list(
          `$set` = list(
            `assets.$.nearby_fire_stations` = nearby$fire_stations,
            `assets.$.nearby_police`        = nearby$police,
            `assets.$.nearby_hospitals`     = nearby$hospitals,
            `assets.$.nearby_supermarkets`  = nearby$supermarkets
          )
        ), auto_unbox=TRUE)
      )
      col3$disconnect()
      n_fire   <- length(nearby$fire_stations)
      n_police <- length(nearby$police)
      n_hosp   <- length(nearby$hospitals)
      n_mkt    <- length(nearby$supermarkets)
      nearby_refresh_rv(paste0(
        "Updated: ", n_fire, " fire station(s), ",
        n_police, " police, ",
        n_hosp, " hospital(s), ", n_mkt, " grocery/supply."
      ))
      asset_mgmt_rv(asset_mgmt_rv() + 1)
    }, error=function(e) {
      tryCatch(col3$disconnect(), error=function(e2) NULL)
      nearby_refresh_rv(paste0("Error saving: ", conditionMessage(e)))
    })
  })

  # ── SNMP Device Management ─────────────────────────────────────────────────
  snmp_device_rv     <- reactiveVal(0)   # triggers re-render on any device change
  snmp_dev_status_rv <- reactiveVal("")
  snmp_offline_rv    <- reactiveVal(0)

  output$snmp_dev_status <- renderText({ snmp_dev_status_rv() })

  # TCP reachability check — runs once, never loops
  .check_snmp_reachability <- function(ip, port) {
    port_int <- as.integer(port)
    if (is.na(port_int) || port_int < 1 || port_int > 65535)
      return(list(status = "error",
                  message = paste0("Invalid port number: ", port)))
    tryCatch({
      con <- socketConnection(host = ip, port = port_int,
                              timeout = 3, open = "r+", blocking = TRUE)
      close(con)
      list(status  = "online",
           message = paste0("TCP connection to ", ip, ":", port_int, " succeeded"))
    }, error = function(e) {
      msg <- conditionMessage(e)
      if (grepl("refused|ECONNREFUSED", msg, ignore.case = TRUE))
        list(status  = "offline",
             message = paste0("Port ", port_int, " actively refused on ", ip,
                              " — host reachable but SNMP/service not listening"))
      else if (grepl("timed out|ETIMEDOUT|timeout|EHOSTUNREACH|unreachable", msg,
                     ignore.case = TRUE))
        list(status  = "unreachable",
             message = paste0("Timed out reaching ", ip, ":", port_int,
                              " — host may be firewalled, powered off, or IP is wrong"))
      else
        list(status  = "error",
             message = paste0("Cannot reach ", ip, ":", port_int, " — ", msg))
    })
  }

  # Read-modify-write helper — updates snmp_devices inside a nested asset array
  .rwm_snmp_devices <- function(uname, asset_id, modify_fn) {
    col <- tryCatch(mongo("users", "weather_rss", url = MONGO_URI),
                    error = function(e) NULL)
    if (is.null(col)) return(FALSE)
    tryCatch({
      u <- col$find(sprintf('{"username":"%s"}', uname),
                    fields = '{"assets":1,"_id":0}')
      if (nrow(u) == 0 || is.null(u$assets)) { col$disconnect(); return(FALSE) }
      assets_raw <- u$assets[[1]]
      .norm <- function(x)
        if (is.data.frame(x) && nrow(x) > 0)
          lapply(seq_len(nrow(x)), function(i) as.list(x[i, ]))
        else if (is.list(x) && length(x) > 0) x else list()
      assets_list <- .norm(assets_raw)
      changed <- FALSE
      for (ai in seq_along(assets_list)) {
        if (isTRUE(as.character(assets_list[[ai]]$asset_id %||% "") == asset_id)) {
          devs <- .norm(assets_list[[ai]]$snmp_devices %||% list())
          assets_list[[ai]]$snmp_devices <- modify_fn(devs)
          changed <- TRUE
          break
        }
      }
      if (!changed) { col$disconnect(); return(FALSE) }
      col$update(sprintf('{"username":"%s"}', uname),
                 sprintf('{"$set":{"assets":%s}}',
                         jsonlite::toJSON(assets_list, auto_unbox = TRUE)))
      col$disconnect()
      TRUE
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL)
      FALSE
    })
  }

  # Collect all offline/unreachable devices across all users
  .get_offline_snmp_devices <- function() {
    col <- tryCatch(mongo("users", "weather_rss", url = MONGO_URI),
                    error = function(e) NULL)
    if (is.null(col)) return(list())
    tryCatch({
      users_data <- col$find('{}', fields = '{"username":1,"assets":1,"_id":0}')
      col$disconnect()
      rows <- list()
      .norm <- function(x)
        if (is.data.frame(x) && nrow(x) > 0)
          lapply(seq_len(nrow(x)), function(i) as.list(x[i, ]))
        else if (is.list(x) && length(x) > 0) x else list()
      for (i in seq_len(nrow(users_data))) {
        uname      <- as.character(users_data$username[i])
        assets_raw <- users_data$assets[[i]]
        if (is.null(assets_raw)) next
        for (asset in .norm(assets_raw)) {
          for (dev in .norm(asset$snmp_devices %||% list())) {
            st <- as.character(dev$status %||% "unknown")
            if (st != "online") {
              rows[[length(rows) + 1]] <- list(
                username     = uname,
                asset_name   = as.character(asset$asset_name %||% ""),
                asset_id     = as.character(asset$asset_id   %||% ""),
                device_id    = as.character(dev$device_id    %||% ""),
                label        = as.character(dev$label        %||% ""),
                ip           = as.character(dev$ip           %||% ""),
                port         = as.integer(dev$port           %||% 161L),
                community    = as.character(dev$community    %||% "public"),
                status       = st,
                message      = as.character(dev$status_message %||% "Not yet checked"),
                last_checked = as.character(dev$last_checked   %||% "Never")
              )
            }
          }
        }
      }
      rows
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL)
      list()
    })
  }

  # ── SNMP Devices sub-panel (shown under selected asset in Config tab) ────────
  output$asset_snmp_devices_panel <- renderUI({
    sel         <- input$user_assets_table_rows_selected
    uname       <- input$asset_mgmt_user
    asset_mgmt_rv()
    snmp_device_rv()
    if (is.null(sel) || length(sel) == 0 ||
        is.null(uname) || nchar(uname) == 0) return(NULL)

    col <- tryCatch(mongo("users", "weather_rss", url = MONGO_URI),
                    error = function(e) NULL)
    if (is.null(col))
      return(div(class = "alert alert-danger", "Database unavailable"))

    asset <- tryCatch({
      u <- col$find(sprintf('{"username":"%s"}', uname),
                    fields = '{"assets":1,"_id":0}')
      col$disconnect()
      if (nrow(u) == 0 || is.null(u$assets)) return(NULL)
      adf <- u$assets[[1]]
      if (is.data.frame(adf) && nrow(adf) >= sel) as.list(adf[sel, ]) else NULL
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL); NULL
    })
    if (is.null(asset)) return(NULL)

    asset_id   <- as.character(asset$asset_id   %||% "")
    asset_name <- as.character(asset$asset_name %||% "")

    .norm <- function(x)
      if (is.data.frame(x) && nrow(x) > 0)
        lapply(seq_len(nrow(x)), function(i) as.list(x[i, ]))
      else if (is.list(x) && length(x) > 0) x else list()
    devices <- .norm(asset$snmp_devices %||% list())

    # Status badge colour/icon helpers
    .st_color <- function(st) switch(st,
      online = "#27ae60", offline = "#e74c3c",
      unreachable = "#e67e22", error = "#c0392b", "#7f8c8d")
    .st_icon <- function(st) switch(st,
      online = "check-circle", offline = "times-circle",
      unreachable = "exclamation-circle", error = "ban", "question-circle")

    dev_rows <- if (length(devices) == 0) {
      list(div(class = "alert alert-info",
               style = "margin-bottom:8px; font-size:13px;",
               icon("network-wired"), " No SNMP devices registered for this asset."))
    } else {
      list(
        tags$table(
          class = "table table-condensed table-bordered",
          style = "font-size:13px; margin-bottom:8px;",
          tags$thead(
            tags$tr(
              tags$th("Label"), tags$th("IP : Port"), tags$th("Community"),
              tags$th("Status"), tags$th("Last Checked"),
              tags$th(style = "max-width:240px;", "Message"), tags$th("Actions")
            )
          ),
          tags$tbody(
            lapply(devices, function(dev) {
              st     <- as.character(dev$status %||% "unknown")
              dev_id <- as.character(dev$device_id %||% "")
              col_   <- .st_color(st)
              ico_   <- .st_icon(st)
              chk_js <- sprintf(
                "Shiny.setInputValue('snmp_dev_action',{action:'check',device_id:'%s',asset_id:'%s',username:'%s'},{priority:'event'})",
                dev_id, asset_id, uname)
              del_js <- sprintf(
                "Shiny.setInputValue('snmp_dev_action',{action:'delete',device_id:'%s',asset_id:'%s',username:'%s'},{priority:'event'})",
                dev_id, asset_id, uname)
              tags$tr(
                tags$td(tags$strong(as.character(dev$label %||% ""))),
                tags$td(tags$code(paste0(dev$ip %||% "", ":", dev$port %||% ""))),
                tags$td(tags$code(as.character(dev$community %||% "public"))),
                tags$td(tags$span(style = paste0("color:", col_, "; font-weight:600;"),
                  icon(ico_), " ", toupper(st))),
                tags$td(tags$small(style = "color:#888;",
                  as.character(dev$last_checked %||% "Never"))),
                tags$td(tags$small(style = "color:#555; word-break:break-word;",
                  as.character(dev$status_message %||% "—"))),
                tags$td(
                  tags$button("Check", class = "btn btn-xs btn-info",
                              style = "margin-right:4px;", onclick = chk_js),
                  tags$button("Delete", class = "btn btn-xs btn-danger",
                              onclick = del_js)
                )
              )
            })
          )
        )
      )
    }

    tagList(
      hr(),
      h5(icon("network-wired"), paste0(" SNMP Devices — ", asset_name)),
      tags$p(tags$small(style = "color:#666;",
        icon("info-circle"),
        " Attach SNMP-accessible devices (switches, routers, servers, APs) to this asset.",
        " Use ", tags$strong("Check"), " to test TCP reachability.",
        tags$span(style = "color:#c0392b; font-weight:600;",
          " Devices are never auto-polled"),
        " — each check runs once and stores the result."
      )),
      dev_rows,
      # ── Add device form ─────────────────────────────────────────────────────
      tags$div(
        style = paste0("background:#f8f9fa; border:1px solid #dee2e6;",
                       " border-radius:4px; padding:12px 12px 4px; margin-top:4px;"),
        tags$h6(icon("plus"), " Add SNMP Device"),
        fluidRow(
          column(3, textInput("snmp_dev_label", "Label",
                              placeholder = "Core Switch", width = "100%")),
          column(3, textInput("snmp_dev_ip", "IP Address / Hostname",
                              placeholder = "192.168.1.1", width = "100%")),
          column(2, numericInput("snmp_dev_port", "Port", value = 161,
                                 min = 1, max = 65535, width = "100%")),
          column(3, textInput("snmp_dev_community", "Community String",
                              placeholder = "public", width = "100%")),
          column(1, br(),
            actionButton("btn_snmp_add_device", "Add",
                         class = "btn-success btn-sm", icon = icon("plus")))
        ),
        tags$small(style = "color:#888;",
          icon("info-circle"),
          " A TCP connection to the specified IP:Port will be attempted immediately to verify reachability.")
      ),
      br(),
      verbatimTextOutput("snmp_dev_status")
    )
  })

  # ── Add SNMP device ─────────────────────────────────────────────────────────
  observeEvent(input$btn_snmp_add_device, {
    if (!isTRUE(auth_rv$role == "admin")) {
      snmp_dev_status_rv("Admin role required."); return()
    }
    uname <- input$asset_mgmt_user
    sel   <- input$user_assets_table_rows_selected
    if (is.null(uname) || nchar(uname) == 0 || is.null(sel) || length(sel) == 0) {
      snmp_dev_status_rv("Select a user and asset row first."); return()
    }
    ip        <- trimws(input$snmp_dev_ip       %||% "")
    port      <- input$snmp_dev_port            %||% 161
    label     <- trimws(input$snmp_dev_label    %||% "")
    community <- trimws(input$snmp_dev_community %||% "public")

    if (nchar(ip) == 0) { snmp_dev_status_rv("IP address or hostname is required."); return() }

    snmp_dev_status_rv(paste0("Checking connectivity to ", ip, ":", port, " ..."))
    check <- .check_snmp_reachability(ip, port)

    # Resolve asset_id for selected row
    col <- tryCatch(mongo("users", "weather_rss", url = MONGO_URI),
                    error = function(e) NULL)
    if (is.null(col)) { snmp_dev_status_rv("Database unavailable."); return() }
    asset_id <- tryCatch({
      u <- col$find(sprintf('{"username":"%s"}', uname),
                    fields = '{"assets":1,"_id":0}')
      col$disconnect()
      if (nrow(u) == 0 || is.null(u$assets)) return(NULL)
      adf <- u$assets[[1]]
      if (is.data.frame(adf) && nrow(adf) >= sel)
        as.character(adf$asset_id[sel]) else NULL
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL); NULL
    })
    if (is.null(asset_id)) {
      snmp_dev_status_rv("Could not identify selected asset."); return()
    }

    new_dev <- list(
      device_id      = paste0(sample(c(letters, LETTERS, 0:9), 12, replace = TRUE),
                              collapse = ""),
      label          = if (nchar(label) == 0) paste0(ip, ":", port) else label,
      ip             = ip,
      port           = as.integer(port),
      community      = if (nchar(community) == 0) "public" else community,
      status         = check$status,
      status_message = check$message,
      last_checked   = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
      added_at       = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
    )

    ok <- .rwm_snmp_devices(uname, asset_id, function(devs) {
      c(devs, list(new_dev))
    })

    if (ok) {
      st_label <- switch(check$status,
        online = "ONLINE", offline = "OFFLINE",
        unreachable = "UNREACHABLE", "ERROR")
      snmp_dev_status_rv(paste0(
        "Device '", new_dev$label, "' added. Initial check: ",
        st_label, " — ", check$message))
      snmp_device_rv(snmp_device_rv() + 1)
    } else {
      snmp_dev_status_rv("Failed to save device to database.")
    }
  })

  # ── Check / Delete device (JS onclick from both Config tab and Alerts tab) ──
  observeEvent(input$snmp_dev_action, {
    if (!isTRUE(auth_rv$role == "admin")) return()
    action    <- as.character(input$snmp_dev_action$action    %||% "")
    device_id <- as.character(input$snmp_dev_action$device_id %||% "")
    asset_id  <- as.character(input$snmp_dev_action$asset_id  %||% "")
    uname     <- as.character(input$snmp_dev_action$username  %||% "")
    if (nchar(device_id) == 0 || nchar(asset_id) == 0 || nchar(uname) == 0) return()

    if (action == "delete") {
      ok <- .rwm_snmp_devices(uname, asset_id, function(devs) {
        Filter(function(d) !isTRUE(d$device_id == device_id), devs)
      })
      if (ok) {
        snmp_dev_status_rv("Device removed.")
        snmp_device_rv(snmp_device_rv() + 1)
      } else {
        snmp_dev_status_rv("Error removing device — check database connection.")
      }

    } else if (action == "check") {
      # Fetch device IP/port from DB
      col <- tryCatch(mongo("users", "weather_rss", url = MONGO_URI),
                      error = function(e) NULL)
      if (is.null(col)) { snmp_dev_status_rv("Database unavailable."); return() }
      dev_info <- tryCatch({
        u <- col$find(sprintf('{"username":"%s"}', uname),
                      fields = '{"assets":1,"_id":0}')
        col$disconnect()
        if (nrow(u) == 0 || is.null(u$assets)) return(NULL)
        .norm <- function(x)
          if (is.data.frame(x) && nrow(x) > 0)
            lapply(seq_len(nrow(x)), function(i) as.list(x[i, ]))
          else if (is.list(x) && length(x) > 0) x else list()
        adf    <- u$assets[[1]]
        a_list <- .norm(adf)
        found  <- NULL
        for (a in a_list) {
          if (isTRUE(as.character(a$asset_id %||% "") == asset_id)) {
            devs <- .norm(a$snmp_devices %||% list())
            hits <- Filter(function(d) isTRUE(d$device_id == device_id), devs)
            if (length(hits) > 0) { found <- hits[[1]]; break }
          }
        }
        found
      }, error = function(e) {
        tryCatch(col$disconnect(), error = function(e2) NULL); NULL
      })
      if (is.null(dev_info)) { snmp_dev_status_rv("Device not found."); return() }

      ip   <- as.character(dev_info$ip   %||% "")
      port <- as.integer(dev_info$port   %||% 161L)
      lbl  <- as.character(dev_info$label %||% paste0(ip, ":", port))
      snmp_dev_status_rv(paste0("Checking ", lbl, " (", ip, ":", port, ") ..."))

      check <- .check_snmp_reachability(ip, port)
      now   <- format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")

      ok <- .rwm_snmp_devices(uname, asset_id, function(devs) {
        lapply(devs, function(d) {
          if (isTRUE(d$device_id == device_id)) {
            d$status         <- check$status
            d$status_message <- check$message
            d$last_checked   <- now
          }
          d
        })
      })

      status_label <- switch(check$status,
        online = "ONLINE", offline = "OFFLINE",
        unreachable = "UNREACHABLE", "ERROR")
      if (ok) {
        snmp_dev_status_rv(paste0(
          lbl, ": ", status_label, " — ", check$message))
      } else {
        snmp_dev_status_rv(paste0(
          "Check completed (", status_label, ") but DB save failed."))
      }
      snmp_device_rv(snmp_device_rv() + 1)
    }
  })

  # ── Offline SNMP Devices panel in Alerts tab ─────────────────────────────────
  output$snmp_offline_devices_ui <- renderUI({
    snmp_device_rv()
    snmp_offline_rv()

    rows <- .get_offline_snmp_devices()

    if (length(rows) == 0) {
      return(div(class = "alert alert-success", style = "font-size:13px;",
        icon("check-circle"), " All registered SNMP devices are online, or none have been added yet."))
    }

    .st_color <- function(st) switch(st,
      online = "#27ae60", offline = "#e74c3c",
      unreachable = "#e67e22", error = "#c0392b", "#7f8c8d")
    .st_icon <- function(st) switch(st,
      online = "check-circle", offline = "times-circle",
      unreachable = "exclamation-circle", error = "ban", "question-circle")

    tags$table(
      class = "table table-condensed table-bordered table-hover",
      style = "font-size:13px;",
      tags$thead(
        tags$tr(
          tags$th("User"), tags$th("Asset"), tags$th("Device"),
          tags$th("IP : Port"), tags$th("Status"), tags$th("Last Checked"),
          tags$th("Message"), tags$th("Action")
        )
      ),
      tags$tbody(
        lapply(rows, function(r) {
          st    <- r$status
          col_  <- .st_color(st)
          ico_  <- .st_icon(st)
          js_chk <- sprintf(
            "Shiny.setInputValue('snmp_dev_action',{action:'check',device_id:'%s',asset_id:'%s',username:'%s'},{priority:'event'})",
            r$device_id, r$asset_id, r$username)
          tags$tr(
            tags$td(tags$code(r$username)),
            tags$td(r$asset_name),
            tags$td(tags$strong(r$label)),
            tags$td(tags$code(paste0(r$ip, ":", r$port))),
            tags$td(tags$span(style = paste0("color:", col_, "; font-weight:600;"),
              icon(ico_), " ", toupper(st))),
            tags$td(tags$small(style = "color:#888;", r$last_checked)),
            tags$td(tags$small(style = "color:#555; word-break:break-word;",
              r$message)),
            tags$td(
              tags$button("Recheck", class = "btn btn-xs btn-warning",
                          onclick = js_chk)
            )
          )
        })
      )
    )
  })

  observeEvent(input$btn_snmp_offline_refresh, {
    snmp_offline_rv(snmp_offline_rv() + 1)
  })

  # Template selector — hidden when "All Facilities" is chosen (uses profession-mapped template)
  output$bcp_template_selector <- renderUI({
    asset_id <- input$bcp_asset_id %||% ""
    if (asset_id == "__all__") {
      div(style="background:#fff3cd;border:1px solid #ffc107;border-radius:4px;padding:8px 12px;margin-bottom:8px;",
        icon("info-circle"),
        tags$small(" Each asset's BCP template is automatically chosen based on the user's profession.",
                   " Broadcast → Broadcast Facility template, Law Enforcement → Campus Police template,",
                   " Emergency Management → County EM template, others → General template.")
      )
    } else {
      selectInput("bcp_template", "BCP Template",
        choices = c(
          "General Facility (default)"    = "general",
          "Broadcast Facility & Staff"    = "broadcast",
          "County Emergency Management"   = "county_em",
          "Campus Police Force"           = "campus_police"
        ), selected = "general")
    }
  })

  # Update asset selector and auto-select BCP template when BCP username changes
  observeEvent(input$bcp_username, {
    uname <- input$bcp_username
    if (is.null(uname) || nchar(uname) == 0) {
      updateSelectInput(session, "bcp_asset_id", choices=c("Select a user first"=""))
      return()
    }
    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) return()
    tryCatch({
      u <- col$find(sprintf('{"username":"%s"}', uname),
                    fields='{"assets":1,"profession":1,"_id":0}')
      col$disconnect()

      # Auto-select BCP template based on profession
      prof <- if (nrow(u) > 0 && !is.null(u$profession) && length(u$profession) > 0)
                as.character(u$profession[[1]]) else ""
      tmpl <- if (nchar(prof) > 0) PROFESSION_TEMPLATE_MAP[prof] %||% "general" else "general"
      updateSelectInput(session, "bcp_template", selected = tmpl)

      # Populate asset dropdown — include "All Facilities" at top
      assets <- if (nrow(u) > 0 && !is.null(u$assets)) u$assets[[1]] else NULL
      if (is.null(assets) || (is.data.frame(assets) && nrow(assets) == 0)) {
        updateSelectInput(session, "bcp_asset_id",
          choices = c("No assets registered for this user" = ""))
        return()
      }
      if (is.data.frame(assets)) {
        asset_choices <- setNames(as.character(assets$asset_id), assets$asset_name)
      } else {
        nms <- sapply(assets, function(a) a$asset_name %||% "Asset")
        ids <- sapply(assets, function(a) a$asset_id   %||% "")
        asset_choices <- setNames(ids, nms)
      }
      updateSelectInput(session, "bcp_asset_id",
        choices = c("All Facilities (batch generate)" = "__all__", asset_choices))
    }, error=function(e) { tryCatch(col$disconnect(), error=function(e2) NULL) })
  })

  # ── BCP Report Generation ────────────────────────────────────────────────────
  bcp_status_msg <- reactiveVal("")
  output$bcp_status <- renderText({ bcp_status_msg() })

  output$profession_bcp_hint <- renderUI({
    prof <- input$new_user_profession %||% ""
    if (nchar(prof) == 0) return(NULL)
    tmpl <- PROFESSION_TEMPLATE_MAP[prof]
    if (is.na(tmpl)) return(NULL)
    info <- switch(tmpl,
      broadcast     = list(icon="broadcast-tower", color="#e07020", label="Broadcast Facility & Staff"),
      campus_police = list(icon="shield-alt",      color="#1c2b6e", label="Campus Police Force"),
      county_em     = list(icon="map",             color="#1a6b3a", label="County Emergency Management"),
      list(icon="info-circle", color="#666", label="General Facility")
    )
    tags$small(style=paste0("color:", info$color, ";"),
      icon(info$icon), " BCP template will auto-select: ", tags$strong(info$label))
  })

  output$bcp_template_desc <- renderUI({
    desc <- switch(input$bcp_template %||% "general",
      general = tags$small(style="color:#666;",
        icon("info-circle"),
        " General facility BCP: weather risk, alerts, traffic, evacuation zones, census, and recommendations."),
      broadcast = tags$small(style="color:#e07020;",
        icon("broadcast-tower"),
        " Broadcast Facility: equipment checklist, staff roles, FCC obligations, on-air continuity, and Icecast stream health."),
      county_em = tags$small(style="color:#1a6b3a;",
        icon("map"),
        " County EM: evacuation zone thresholds, EOC activation levels, mass notification coordination, and FEMA recovery timeline."),
      campus_police = tags$small(style="color:#1c2b6e;",
        icon("shield-alt"),
        " Campus Police: sector deployment, vulnerable population protocols, inter-agency coordination, and vehicle/equipment checklists.")
    )
    div(style="margin:-6px 0 8px 0;", desc)
  })

  # ── BCP report helpers ────────────────────────────────────────────────────────

  # Parse a bcp_Label_uname_asset_date_time.pdf filename into a data frame row
  .parse_bcp_filename <- function(fn) {
    base  <- gsub("\\.pdf$", "", fn)
    parts <- strsplit(base, "_")[[1]]
    # parts: [1]="bcp" [2]=Label [3]=username [4..n-2]=asset words [n-1]=date [n]=time
    n     <- length(parts)
    tmpl  <- if (n >= 2) parts[2] else "?"
    user  <- if (n >= 3) parts[3] else "?"
    asset <- if (n >= 6) paste(parts[4:(n-2)], collapse = " ") else if (n >= 4) parts[4] else "?"
    ts    <- if (n >= 2) {
      d <- parts[n-1]; t <- parts[n]
      paste0(
        if (nchar(d) == 8) paste0(substr(d,1,4),"-",substr(d,5,6),"-",substr(d,7,8)) else d,
        " ",
        if (nchar(t) >= 4) paste0(substr(t,1,2),":",substr(t,3,4)) else t
      )
    } else "?"
    data.frame(Template=tmpl, User=user, Asset=asset, Generated=ts,
               File=fn, stringsAsFactors=FALSE)
  }

  # Reactive: list of BCP files visible to the current user
  .bcp_files_for_user <- function(trigger=NULL) {
    force(trigger)
    uname <- auth_rv$username %||% ""
    role  <- auth_rv$role  %||% ""
    all_f <- list.files(rpt_output_dir, pattern="^bcp_.*\\.pdf$", full.names=FALSE)
    if (role != "admin" && nchar(uname) > 0) {
      pat   <- paste0("^bcp_[^_]+_", uname, "_")
      all_f <- all_f[grepl(pat, all_f)]
    }
    sort(all_f, decreasing=TRUE)
  }

  # Shared reactive for BCP file list (invalidates when BCP is generated)
  bcp_files_rv <- reactive({
    input$btn_gen_bcp
    .bcp_files_for_user()
  })

  # Helper: build datatable from file list
  .bcp_datatable <- function(files, label="No BCP reports yet.") {
    if (length(files) == 0)
      return(datatable(data.frame(Message=label), options=list(dom="t"), rownames=FALSE))
    df <- do.call(rbind, lapply(files, .parse_bcp_filename))
    datatable(
      df[, c("Template","User","Asset","Generated","File")],
      options = list(
        pageLength = 8, scrollX = TRUE, dom = "ftp",
        columnDefs = list(list(targets = 4, visible = FALSE))  # hide File col
      ),
      rownames  = FALSE,
      selection = "single"
    )
  }

  # ── BCP section table (Reports > BCP section) ─────────────────────────────
  selected_bcp_file      <- reactiveVal(NULL)
  bcp_report_action_status_rv <- reactiveVal("")
  output$bcp_report_action_status <- renderText(bcp_report_action_status_rv())

  output$tbl_bcp_reports <- renderDT({
    .bcp_datatable(bcp_files_rv())
  }, server = FALSE)

  observeEvent(input$tbl_bcp_reports_rows_selected, {
    idx <- input$tbl_bcp_reports_rows_selected
    files <- bcp_files_rv()
    if (!is.null(idx) && length(idx) > 0 && idx <= length(files))
      selected_bcp_file(files[idx])
    else
      selected_bcp_file(NULL)
  })

  output$dl_bcp_report <- downloadHandler(
    filename = function() selected_bcp_file() %||% "bcp_report.pdf",
    content  = function(file) {
      src <- file.path(rpt_output_dir, selected_bcp_file() %||% "")
      if (file.exists(src)) file.copy(src, file)
    }
  )

  observeEvent(input$btn_email_bcp_report, {
    fn <- selected_bcp_file()
    if (is.null(fn)) { bcp_report_action_status_rv("Select a report row first."); return() }
    src <- file.path(rpt_output_dir, fn)
    u_email <- tryCatch({
      col2 <- mongo(collection="users", db="weather_rss", url=MONGO_URI)
      ue   <- col2$find(sprintf('{"username":"%s"}', auth_rv$username),
                        fields='{"email":1,"_id":0}')
      col2$disconnect()
      if (nrow(ue) > 0 && !is.null(ue$email)) as.character(ue$email[1]) else ""
    }, error=function(e) "")
    if (nchar(u_email) == 0) { bcp_report_action_status_rv("No email on file for your account."); return() }
    tryCatch({
      send_fpren_email(u_email,
        paste0("FPREN BCP Report: ", fn),
        paste0("<h3>Business Continuity Plan</h3>",
               "<p>Your requested BCP report (<strong>", fn, "</strong>) is attached.</p>"),
        attachment_path = src)
      bcp_report_action_status_rv(paste0("Sent to ", u_email))
    }, error=function(e) bcp_report_action_status_rv(paste0("Error: ", conditionMessage(e))))
  })

  # ── Past BCP Reports box (Reports tab) ────────────────────────────────────
  selected_past_bcp_file      <- reactiveVal(NULL)
  past_bcp_action_status_rv   <- reactiveVal("")
  output$past_bcp_action_status <- renderText(past_bcp_action_status_rv())

  output$tbl_past_bcp_reports <- renderDT({
    input$btn_gen_bcp  # refresh when a new BCP is generated
    .bcp_datatable(.bcp_files_for_user(), "No BCP reports associated with your profile yet.")
  }, server = FALSE)

  observeEvent(input$tbl_past_bcp_reports_rows_selected, {
    idx   <- input$tbl_past_bcp_reports_rows_selected
    files <- .bcp_files_for_user()
    if (!is.null(idx) && length(idx) > 0 && idx <= length(files))
      selected_past_bcp_file(files[idx])
    else
      selected_past_bcp_file(NULL)
  })

  output$dl_past_bcp_report <- downloadHandler(
    filename = function() selected_past_bcp_file() %||% "bcp_report.pdf",
    content  = function(file) {
      src <- file.path(rpt_output_dir, selected_past_bcp_file() %||% "")
      if (file.exists(src)) file.copy(src, file)
    }
  )

  observeEvent(input$btn_email_past_bcp_report, {
    fn <- selected_past_bcp_file()
    if (is.null(fn)) { past_bcp_action_status_rv("Select a report row first."); return() }
    src <- file.path(rpt_output_dir, fn)
    u_email <- tryCatch({
      col2 <- mongo(collection="users", db="weather_rss", url=MONGO_URI)
      ue   <- col2$find(sprintf('{"username":"%s"}', auth_rv$username),
                        fields='{"email":1,"_id":0}')
      col2$disconnect()
      if (nrow(ue) > 0 && !is.null(ue$email)) as.character(ue$email[1]) else ""
    }, error=function(e) "")
    if (nchar(u_email) == 0) { past_bcp_action_status_rv("No email on file for your account."); return() }
    tryCatch({
      send_fpren_email(u_email,
        paste0("FPREN BCP Report: ", fn),
        paste0("<h3>Business Continuity Plan</h3>",
               "<p>Your requested BCP report (<strong>", fn, "</strong>) is attached.</p>"),
        attachment_path = src)
      past_bcp_action_status_rv(paste0("Sent to ", u_email))
    }, error=function(e) past_bcp_action_status_rv(paste0("Error: ", conditionMessage(e))))
  })

  # Helper: render one BCP PDF for a single asset
  # all_assets: data frame of all user assets (sorted by priority); used for cross-asset section
  .render_one_bcp <- function(uname, asset, tmpl_key, output_dir, timestamp,
                              all_assets = NULL, profession = "General") {
    template_map <- list(
      general       = "/home/ufuser/Fpren-main/reports/business_continuity_report.Rmd",
      broadcast     = "/home/ufuser/Fpren-main/reports/bcp_broadcast_facility.Rmd",
      county_em     = "/home/ufuser/Fpren-main/reports/bcp_county_em.Rmd",
      campus_police = "/home/ufuser/Fpren-main/reports/bcp_campus_police.Rmd"
    )
    tmpl_file  <- template_map[[tmpl_key]] %||% template_map[["general"]]
    tmpl_label <- switch(tmpl_key,
      broadcast="Broadcast", county_em="CountyEM", campus_police="CampusPolice", "General")
    safe_name  <- gsub("[^A-Za-z0-9]", "_", asset$asset_name %||% "asset")
    out_file   <- file.path(output_dir,
      paste0("bcp_", tmpl_label, "_", uname, "_", safe_name, "_", timestamp, ".pdf"))
    lat <- tryCatch(as.numeric(asset$lat), error=function(e) 29.65)
    lon <- tryCatch(as.numeric(asset$lon), error=function(e) -82.33)
    if (is.na(lat)) lat <- 29.65
    if (is.na(lon)) lon <- -82.33

    # Build JSON of all other assets (excluding this one) for the cross-asset section
    other_assets_json <- tryCatch({
      this_id <- as.character(asset$asset_id %||% "")
      if (!is.null(all_assets) && is.data.frame(all_assets) && nrow(all_assets) > 0) {
        others <- all_assets[as.character(all_assets$asset_id) != this_id, , drop=FALSE]
        if (nrow(others) > 0) jsonlite::toJSON(others, auto_unbox=TRUE) else "[]"
      } else "[]"
    }, error=function(e) "[]")

    tmp <- file.path(tempdir(), paste0("bcp_", safe_name, "_", format(Sys.time(),"%H%M%S")))
    dir.create(tmp, showWarnings=FALSE)
    withr::with_dir(tmp, rmarkdown::render(
      input             = tmpl_file,
      output_file       = out_file,
      intermediates_dir = tmp,
      params = list(
        username             = uname,
        asset_name           = asset$asset_name          %||% "",
        address              = asset$address             %||% "",
        lat                  = lat, lon = lon,
        zip                  = asset$zip                 %||% "",
        city                 = asset$city                %||% "",
        nearest_airport_icao = asset$nearest_airport_icao %||% "KGNV",
        nearest_airport_name = asset$nearest_airport_name %||% "Gainesville Regional",
        asset_type           = asset$asset_type          %||% "Facility",
        notes                = asset$notes               %||% "",
        other_assets_json    = other_assets_json,
        profession           = profession,
        mongo_uri            = MONGO_URI, days_back = 30L
      ), quiet = TRUE
    ))
    out_file
  }

  observeEvent(input$btn_gen_bcp, {
    if (!isTRUE(auth_rv$role %in% c("operator","admin"))) {
      bcp_status_msg("Access denied."); return()
    }
    uname    <- input$bcp_username
    asset_id <- input$bcp_asset_id
    if (is.null(uname) || nchar(uname) == 0 || is.null(asset_id) || nchar(asset_id) == 0) {
      bcp_status_msg("Select a user and asset first."); return()
    }

    # ── All Facilities batch mode ────────────────────────────────────────────
    if (asset_id == "__all__") {
      bcp_status_msg("Generating BCPs for all facilities\u2026 (this may take several minutes)")
      col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
      if (is.null(col)) { bcp_status_msg("DB unavailable."); return() }
      tryCatch({
        all_users <- col$find("{}", fields='{"username":1,"profession":1,"assets":1,"email":1,"_id":0}')
        col$disconnect()
        output_dir <- rpt_output_dir
        dir.create(output_dir, showWarnings=FALSE, recursive=TRUE)
        timestamp  <- format(Sys.time(), "%Y%m%d_%H%M")
        generated  <- character(0)
        errors     <- character(0)

        for (i in seq_len(nrow(all_users))) {
          row    <- all_users[i, ]
          uname_i <- as.character(row$username)
          prof_i  <- if (!is.null(row$profession) && !is.na(row$profession)) as.character(row$profession) else ""
          tmpl_i  <- if (nchar(prof_i) > 0) PROFESSION_TEMPLATE_MAP[prof_i] %||% "general" else "general"
          assets_i <- if (!is.null(row$assets)) row$assets[[1]] else NULL
          if (is.null(assets_i) || (is.data.frame(assets_i) && nrow(assets_i) == 0)) next

          # Sort by priority if available
          if (is.data.frame(assets_i) && "priority" %in% names(assets_i))
            assets_i <- assets_i[order(as.integer(assets_i$priority)), ]

          all_assets_i <- assets_i  # pass full sorted set to each render call

          asset_list <- if (is.data.frame(assets_i)) {
            lapply(seq_len(nrow(assets_i)), function(j) as.list(assets_i[j, ]))
          } else as.list(assets_i)

          for (asset in asset_list) {
            tryCatch({
              f <- .render_one_bcp(uname_i, asset, tmpl_i, output_dir, timestamp,
                                   all_assets = all_assets_i, profession = prof_i %||% "General")
              generated <- c(generated, basename(f))
              if (isTRUE(input$bcp_email) && !is.null(row$email) && nchar(as.character(row$email)) > 0) {
                send_fpren_email(as.character(row$email),
                  paste0("FPREN BCP: ", asset$asset_name %||% "Asset"),
                  paste0("<h3>Business Continuity Plan</h3><p>BCP for <strong>",
                         asset$asset_name %||% "Asset", "</strong> is attached.</p>"),
                  attachment_path = f)
              }
            }, error=function(e) {
              errors <<- c(errors, paste0(uname_i, "/", asset$asset_name %||% "?", ": ", conditionMessage(e)))
            })
          }
        }
        msg <- paste0("Generated ", length(generated), " BCP(s).")
        if (length(errors) > 0) msg <- paste0(msg, "\nErrors: ", paste(errors, collapse="; "))
        bcp_status_msg(msg)
        showNotification(paste0("Batch BCP complete: ", length(generated), " files"), type="message")
      }, error=function(e) {
        tryCatch(col$disconnect(), error=function(e2) NULL)
        bcp_status_msg(paste0("Batch error: ", conditionMessage(e)))
      })
      return()
    }
    bcp_status_msg("Generating BCP — this may take 60-120 seconds...")

    # Fetch asset details + profession from MongoDB
    col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) { bcp_status_msg("DB unavailable."); return() }
    user_profession <- tryCatch({
      up <- col$find(sprintf('{"username":"%s"}', uname), fields='{"profession":1,"_id":0}')
      if (nrow(up) > 0 && !is.null(up$profession) && !is.na(up$profession)) as.character(up$profession) else "General"
    }, error = function(e) "General")
    asset <- tryCatch({
      u <- col$find(sprintf('{"username":"%s"}', uname), fields='{"assets":1,"_id":0}')
      col$disconnect()
      assets <- if (nrow(u) > 0 && !is.null(u$assets)) u$assets[[1]] else NULL
      if (is.null(assets)) NULL else {
        if (is.data.frame(assets)) {
          idx <- which(as.character(assets$asset_id) == asset_id)
          if (length(idx) == 0) NULL else as.list(assets[idx[1], ])
        } else {
          found <- Filter(function(a) as.character(a$asset_id) == asset_id, assets)
          if (length(found) == 0) NULL else found[[1]]
        }
      }
    }, error=function(e) {
      tryCatch(col$disconnect(), error=function(e2) NULL); NULL
    })

    if (is.null(asset)) { bcp_status_msg("Asset not found."); return() }

    output_dir  <- rpt_output_dir
    dir.create(output_dir, showWarnings=FALSE, recursive=TRUE)
    timestamp   <- format(Sys.time(), "%Y%m%d_%H%M")
    tmpl_key    <- input$bcp_template %||% "general"
    all_assets  <- .load_user_assets(uname)  # sorted by priority

    tryCatch({
      output_file <- .render_one_bcp(uname, asset, tmpl_key, output_dir, timestamp,
                                     all_assets = all_assets, profession = user_profession)
      msg <- paste0("BCP saved: ", basename(output_file))
      if (isTRUE(input$bcp_email)) {
        u_email <- tryCatch({
          col2 <- mongo(collection="users", db="weather_rss", url=MONGO_URI)
          ue <- col2$find(sprintf('{"username":"%s"}', uname), fields='{"email":1,"_id":0}')
          col2$disconnect()
          if (nrow(ue) > 0 && !is.null(ue$email)) ue$email[1] else ""
        }, error=function(e) "")
        if (nchar(u_email) > 0) {
          send_fpren_email(u_email,
            paste0("FPREN BCP: ", asset$asset_name %||% "Asset"),
            paste0("<h3>Business Continuity Plan</h3>",
                   "<p>Your BCP for <strong>", asset$asset_name %||% "Asset",
                   "</strong> has been generated and is attached.</p>"),
            attachment_path = output_file)
          msg <- paste0(msg, " — emailed to ", u_email)
        }
      }
      bcp_status_msg(msg)
    }, error=function(e) bcp_status_msg(paste0("ERROR: ", conditionMessage(e))))
  })

  # Upload Content (operator + admin only)
  CONTENT_ROOT <- "/home/ufuser/Fpren-main/weather_station/audio/content"
  upload_msg <- reactiveVal("")
  output$upload_file_list <- DT::renderDataTable({
    if (!isTRUE(auth_rv$role %in% c("operator","admin"))) return(data.frame(Access="Operator or Admin role required."))
    input$btn_upload; input$upload_folder
    folder <- file.path(CONTENT_ROOT, input$upload_folder)
    if (!dir.exists(folder)) return(data.frame(Message="Folder not found"))
    files <- list.files(folder, pattern="\\.(mp3|wav|ogg|m4a)$", ignore.case=TRUE)
    if (length(files)==0) return(data.frame(Message="No files yet"))
    data.frame(Filename=files, Size_KB=file.size(file.path(folder,files))%/%1024,
               stringsAsFactors=FALSE)
  }, options=list(pageLength=20), rownames=FALSE)
  observeEvent(input$btn_upload, {
    if (!isTRUE(auth_rv$role %in% c("operator","admin"))) { upload_msg("Access denied."); return() }
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
    # playlist_order comes from the JS drag events; fall back to types order
    order_str <- input$playlist_order %||% ""
    ordered_types <- if (nchar(order_str) > 0) {
      order_all <- trimws(unlist(strsplit(order_str, ",")))
      # Keep only checked types, preserving drag order
      intersect(order_all, types)
    } else {
      types
    }
    col <- get_col("zone_definitions")
    if (is.null(col)) { pl_save_status("Error: MongoDB unavailable."); return() }
    tryCatch({
      types_json <- paste0('["', paste(ordered_types, collapse='","'), '"]')
      col$update(sprintf('{"zone_id":"%s"}', zone_id),
                 sprintf('{"$set":{"normal_mode_types":%s}}', types_json))
      col$disconnect()
      pl_save_status(sprintf("Saved %d types for %s (priority order preserved) at %s",
                             length(ordered_types), zone_id,
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

  # ── Census & Demographics Tab ─────────────────────────────────────────────
  CENSUS_API <- "http://localhost:5000/api/census"

  census_all_rv     <- reactiveVal(NULL)
  census_county_rv  <- reactiveVal(NULL)
  census_ai_rv      <- reactiveVal("")
  census_impact_rv      <- reactiveVal(NULL)
  census_impact_data_rv <- reactiveVal(NULL)   # raw API response for PDF/email
  census_impact_status_rv <- reactiveVal("")
  census_refresh_rv <- reactiveVal("")

  # Load all counties on tab visit
  census_data_loaded <- reactiveVal(FALSE)
  observe({
    if (!isTRUE(auth_rv$logged_in)) return()
    if (census_data_loaded()) return()
    tryCatch({
      r   <- httr::GET(paste0(CENSUS_API, "/counties"), httr::timeout(15))
      dat <- httr::content(r, as = "parsed", type = "application/json")
      if (isTRUE(dat$ok) && length(dat$counties) > 0) {
        counties <- dat$counties
        census_data_loaded(TRUE)
        census_all_rv(counties)
        cnames <- sapply(counties, function(x) x$county)
        updateSelectInput(session, "census_county_sel",
          choices = setNames(cnames, cnames), selected = "Alachua")
      }
    }, error = function(e) NULL)
  })

  # Load active alerts for impact selector
  observe({
    if (!isTRUE(auth_rv$logged_in)) return()
    col <- tryCatch(mongo(collection="nws_alerts", db="weather_rss", url=MONGO_URI), error=function(e) NULL)
    if (is.null(col)) return()
    tryCatch({
      alerts <- col$find(
        '{}',
        fields = '{"alert_id":1,"event":1,"area_desc":1,"severity":1,"fetched_at":1,"_id":1}',
        sort   = '{"fetched_at":-1}',
        limit  = 50
      )
      col$disconnect()
      if (nrow(alerts) > 0) {
        labels <- paste0(alerts$event, " — ", substr(alerts$area_desc, 1, 45))
        ids    <- if (!is.null(alerts$alert_id) && any(nchar(as.character(alerts$alert_id)) > 0))
                    as.character(alerts$alert_id)
                  else
                    as.character(alerts$`_id`)
        updateSelectInput(session, "census_alert_sel",
          choices = c("-- select alert --" = "", setNames(ids, labels)))
      } else {
        updateSelectInput(session, "census_alert_sel",
          choices = c("No alerts in database" = ""))
      }
    }, error = function(e) {
      tryCatch(col$disconnect(), error=function(e2) NULL)
    })
  })

  # Value boxes — statewide totals
  output$census_vbox_pop <- renderValueBox({
    counties <- census_all_rv()
    total <- if (!is.null(counties)) sum(sapply(counties, function(x) x$population_total %||% 0)) else 0
    valueBox(format(total, big.mark=","), "FL Total Population", icon=icon("users"), color="blue")
  })
  output$census_vbox_elderly <- renderValueBox({
    counties <- census_all_rv()
    avg_pct <- if (!is.null(counties) && length(counties) > 0)
      round(mean(sapply(counties, function(x) x$pct_65plus %||% 0)), 1) else 0
    valueBox(paste0(avg_pct, "%"), "Avg % Age 65+", icon=icon("user-friends"), color="yellow")
  })
  output$census_vbox_poverty <- renderValueBox({
    counties <- census_all_rv()
    avg_pct <- if (!is.null(counties) && length(counties) > 0)
      round(mean(sapply(counties, function(x) x$pct_poverty %||% 0)), 1) else 0
    valueBox(paste0(avg_pct, "%"), "Avg % Below Poverty", icon=icon("dollar-sign"), color="orange")
  })
  output$census_vbox_vulnerable <- renderValueBox({
    counties <- census_all_rv()
    n_high <- if (!is.null(counties))
      sum(sapply(counties, function(x) (x$vulnerability_score %||% 0) >= 0.5)) else 0
    valueBox(n_high, "High-Vulnerability Counties", icon=icon("exclamation-triangle"), color="red")
  })

  # County detail panel
  output$census_county_detail <- renderUI({
    county_name <- input$census_county_sel
    counties    <- census_all_rv()
    if (is.null(counties) || is.null(county_name) || nchar(county_name) == 0)
      return(p("Select a county to view demographics."))
    cdata <- Filter(function(x) x$county == county_name, counties)
    if (length(cdata) == 0) return(p("No data for this county."))
    d <- cdata[[1]]
    score <- d$vulnerability_score %||% 0
    sc_col <- if (score >= 0.7) "red" else if (score >= 0.5) "orange" else if (score >= 0.3) "#0077aa" else "green"
    tagList(
      fluidRow(
        column(3, div(style="text-align:center;",
          div(style="font-size:28px;font-weight:bold;color:#003087;",
              format(d$population_total %||% 0, big.mark=",")),
          div(style="font-size:12px;color:#888;", "Total Population")
        )),
        column(3, div(style="text-align:center;",
          div(style=paste0("font-size:28px;font-weight:bold;color:",sc_col,";"),
              round(score, 3)),
          div(style="font-size:12px;color:#888;", paste("Vulnerability:", d$vulnerability_label %||% ""))
        )),
        column(3, div(style="text-align:center;",
          div(style="font-size:28px;font-weight:bold;color:#e07020;",
              paste0(d$pct_65plus %||% 0, "%")),
          div(style="font-size:12px;color:#888;", "Age 65+")
        )),
        column(3, div(style="text-align:center;",
          div(style="font-size:28px;font-weight:bold;color:#c0392b;",
              paste0(d$pct_poverty %||% 0, "%")),
          div(style="font-size:12px;color:#888;", "Below Poverty")
        ))
      ),
      hr(),
      fluidRow(
        column(6, tags$table(class="table table-condensed", style="font-size:13px;",
          tags$tr(tags$td(strong("Limited English")),  tags$td(paste0(d$pct_limited_english %||% 0, "%"))),
          tags$tr(tags$td(strong("With Disability")),  tags$td(paste0(d$pct_disability %||% 0, "%"))),
          tags$tr(tags$td(strong("Under 18")),         tags$td(paste0(d$pct_under18 %||% 0, "%")))
        )),
        column(6, tags$table(class="table table-condensed", style="font-size:13px;",
          tags$tr(tags$td(strong("Median Income")),    tags$td(paste0("$", format(d$median_household_income %||% 0, big.mark=",")))),
          tags$tr(tags$td(strong("Housing Units")),    tags$td(format(d$housing_units %||% 0, big.mark=","))),
          tags$tr(tags$td(strong("ACS Year")),         tags$td(d$year %||% ""))
        ))
      )
    )
  })

  # Vulnerability bar chart for top 20 counties
  output$census_vulnerability_chart <- renderPlot({
    counties <- census_all_rv()
    if (is.null(counties) || length(counties) == 0) return(NULL)
    df <- data.frame(
      county = sapply(counties, function(x) x$county),
      score  = sapply(counties, function(x) x$vulnerability_score %||% 0),
      stringsAsFactors = FALSE
    )
    df <- head(df[order(-df$score), ], 20)
    df$county <- factor(df$county, levels = rev(df$county))
    df$fill_col <- ifelse(df$score >= 0.7, "#c0392b",
                   ifelse(df$score >= 0.5, "#e07020",
                   ifelse(df$score >= 0.3, "#0077aa", "#27ae60")))
    par(mar = c(3, 7, 1.5, 1))
    barplot(df$score, names.arg = df$county, horiz = TRUE, las = 1,
            col = df$fill_col, border = NA, cex.names = 0.75,
            xlab = "Vulnerability Score (0–1)", main = "Top 20 Most Vulnerable FL Counties",
            xlim = c(0, 1))
    abline(v = c(0.3, 0.5, 0.7), lty = 2, col = c("#0077aa","#e07020","#c0392b"), lwd = 1)
  })

  # AI vulnerability analysis
  output$census_ai_output <- renderText({ census_ai_rv() })

  observeEvent(input$btn_census_analyze, {
    county_name <- input$census_county_sel
    if (is.null(county_name) || nchar(county_name) == 0) {
      census_ai_rv("Select a county first."); return()
    }
    census_ai_rv("Requesting AI analysis from LiteLLM...")
    tryCatch({
      r   <- httr::GET(paste0(CENSUS_API, "/analysis/", URLencode(county_name)),
                       httr::timeout(30))
      dat <- httr::content(r, as = "parsed", type = "application/json")
      if (isTRUE(dat$ok)) {
        census_ai_rv(dat$analysis %||% "No analysis returned.")
      } else {
        census_ai_rv(paste0("Error: ", dat$message %||% "Unknown error"))
      }
    }, error = function(e) census_ai_rv(paste0("Request failed: ", e$message)))
  })

  # Population impact for active alert
  output$census_impact_output <- renderUI({
    census_impact_rv()
  })

  output$census_impact_status <- renderText({ census_impact_status_rv() })

  observeEvent(input$btn_census_impact, {
    alert_id <- input$census_alert_sel
    if (is.null(alert_id) || nchar(alert_id) == 0) {
      census_impact_rv(p("Select an active alert first.")); return()
    }
    census_impact_data_rv(NULL)
    census_impact_rv(p(icon("spinner"), " Analyzing population impact..."))
    tryCatch({
      r   <- httr::GET(paste0(CENSUS_API, "/impact/", URLencode(alert_id)),
                       httr::timeout(30))
      dat <- httr::content(r, as = "parsed", type = "application/json")
      if (isTRUE(dat$ok)) {
        census_impact_data_rv(dat$alert)   # store raw data for PDF/email
        imp       <- dat$alert$census_impact
        n_at_risk <- format(imp$total_population_at_risk %||% 0, big.mark=",")
        census_impact_rv(tagList(
          div(style="background:#fff3cd;border:1px solid #ffc107;border-radius:4px;padding:10px 14px;margin-bottom:10px;",
            strong("Total population in affected counties: "), n_at_risk
          ),
          p(strong("AI Analysis:")),
          pre(style="background:#f9f9f9;padding:10px;border-radius:4px;white-space:pre-wrap;font-size:13px;",
              imp$ai_analysis %||% "No analysis available.")
        ))
      } else {
        census_impact_rv(p(style="color:red;", "Error: ", dat$message %||% "Unknown"))
      }
    }, error = function(e) census_impact_rv(p(style="color:red;", "Request failed: ", e$message)))
  })

  # ── Census Impact PDF export ─────────────────────────────────────────────
  observeEvent(input$btn_census_impact_pdf, {
    dat <- census_impact_data_rv()
    if (is.null(dat)) {
      census_impact_status_rv("Run Analyze Impact first.")
      return()
    }
    census_impact_status_rv("Generating PDF\u2026 (30\u201360 s)")
    tryCatch({
      imp         <- dat$census_impact
      output_dir  <- "/home/ufuser/Fpren-main/reports/output"
      dir.create(output_dir, showWarnings=FALSE, recursive=TRUE)
      timestamp   <- format(Sys.time(), "%Y%m%d_%H%M")
      safe_event  <- gsub("[^A-Za-z0-9]", "_", dat$event %||% "alert")
      output_file <- file.path(output_dir,
        paste0("census_impact_", safe_event, "_", timestamp, ".pdf"))

      counties_json <- tryCatch(
        jsonlite::toJSON(imp$county_data %||% list(), auto_unbox=TRUE),
        error = function(e) "{}"
      )

      withr::with_dir(tempdir(), rmarkdown::render(
        input             = "/home/ufuser/Fpren-main/reports/census_impact_report.Rmd",
        output_file       = output_file,
        intermediates_dir = tempdir(),
        params = list(
          alert_event       = dat$event       %||% "",
          alert_area        = dat$area_desc   %||% "",
          alert_severity    = dat$severity    %||% "",
          alert_headline    = dat$headline    %||% "",
          alert_description = dat$description %||% "",
          total_population  = imp$total_population_at_risk %||% 0,
          counties_json     = as.character(counties_json),
          ai_analysis       = imp$ai_analysis %||% "",
          mongo_uri         = MONGO_URI
        ),
        quiet = TRUE
      ))
      census_impact_status_rv(paste0("Saved: ", basename(output_file)))
      showNotification(paste0("PDF saved: ", basename(output_file)), type="message")
    }, error = function(e) {
      census_impact_status_rv(paste0("PDF error: ", conditionMessage(e)))
      showNotification(conditionMessage(e), type="error")
    })
  })

  # ── Census Impact email ──────────────────────────────────────────────────
  observeEvent(input$btn_census_impact_email, {
    dat <- census_impact_data_rv()
    if (is.null(dat)) {
      census_impact_status_rv("Run Analyze Impact first, then Export PDF before emailing.")
      return()
    }
    # Find the most recent census impact PDF
    output_dir  <- "/home/ufuser/Fpren-main/reports/output"
    safe_event  <- gsub("[^A-Za-z0-9]", "_", dat$event %||% "alert")
    files <- list.files(output_dir,
      pattern    = paste0("^census_impact_", safe_event, "_.*\\.pdf$"),
      full.names = TRUE)
    if (length(files) == 0) {
      census_impact_status_rv("No PDF found \u2014 click Export PDF first.")
      return()
    }
    latest_file <- files[which.max(file.mtime(files))]
    census_impact_status_rv("Sending email\u2026")
    tryCatch({
      sc        <- tryCatch(
        jsonlite::fromJSON("/home/ufuser/Fpren-main/weather_rss/config/smtp_config.json"),
        error = function(e) list())
      smtp_host <- sc$smtp_host %||% "smtp.ufl.edu"
      smtp_port <- as.integer(sc$smtp_port %||% 25)
      mail_from <- sc$mail_from %||% "lawrence.bornace@ufl.edu"
      mail_to   <- sc$mail_to   %||% "lawrence.bornace@ufl.edu"
      imp       <- dat$census_impact
      subject   <- sprintf("FPREN Alert Impact Report — %s — %s",
                           dat$event %||% "Alert", format(Sys.Date(), "%Y-%m-%d"))
      library(emayili)
      em <- envelope() %>%
        from(mail_from) %>%
        to(mail_to) %>%
        subject(subject) %>%
        text(paste0(
          "FPREN Alert Population Impact Report\n\n",
          "Alert:      ", dat$event %||% "", "\n",
          "Severity:   ", dat$severity %||% "", "\n",
          "Area:       ", dat$area_desc %||% "", "\n",
          "Population: ", format(imp$total_population_at_risk %||% 0, big.mark=","), "\n",
          "Date:       ", format(Sys.Date(), "%Y-%m-%d"), "\n\n",
          "AI Analysis:\n", imp$ai_analysis %||% "", "\n\n",
          "PDF report attached.\n\n",
          "-- FPREN Automated Reporting System\n",
          "   Florida Public Radio Emergency Network\n"
        )) %>%
        attachment(latest_file)
      server(host=smtp_host, port=smtp_port, reuse=FALSE)(em, verbose=FALSE)
      msg <- paste0("Email sent to ", mail_to, " at ", format(Sys.time(), "%H:%M:%S"))
      census_impact_status_rv(msg)
      showNotification(msg, type="message")
    }, error = function(e) {
      census_impact_status_rv(paste0("Email error: ", conditionMessage(e)))
      showNotification(conditionMessage(e), type="error")
    })
  })

  # All counties data table
  output$census_all_table <- DT::renderDataTable({
    counties <- census_all_rv()
    if (is.null(counties) || length(counties) == 0)
      return(datatable(data.frame(Message="No census data loaded. Run the fetcher first.")))
    df <- data.frame(
      County          = sapply(counties, function(x) x$county),
      Population      = sapply(counties, function(x) format(x$population_total %||% 0, big.mark=",")),
      "65+"           = sapply(counties, function(x) paste0(x$pct_65plus %||% 0, "%")),
      Poverty         = sapply(counties, function(x) paste0(x$pct_poverty %||% 0, "%")),
      "Lim.English"   = sapply(counties, function(x) paste0(x$pct_limited_english %||% 0, "%")),
      Disability      = sapply(counties, function(x) paste0(x$pct_disability %||% 0, "%")),
      Score           = sapply(counties, function(x) round(x$vulnerability_score %||% 0, 3)),
      Level           = sapply(counties, function(x) x$vulnerability_label %||% ""),
      stringsAsFactors = FALSE,
      check.names = FALSE
    )
    dt <- datatable(df, options=list(pageLength=20, scrollX=TRUE, order=list(list(7,"desc"))),
                    rownames=FALSE, selection="none")
    dt %>% DT::formatStyle("Level",
      backgroundColor = DT::styleEqual(
        c("Critical","High","Moderate","Low"),
        c("#fadbd8", "#fdebd0", "#d6eaf8", "#d5f5e3")
      ))
  }, server = FALSE)

  # Admin census refresh
  output$census_refresh_status <- renderText({ census_refresh_rv() })

  observeEvent(input$btn_census_refresh, {
    if (!isTRUE(auth_rv$role == "admin")) { census_refresh_rv("Admin required."); return() }
    census_refresh_rv("Fetching from Census API...")
    tryCatch({
      r   <- httr::POST(paste0(CENSUS_API, "/refresh"),
                        httr::add_headers("Content-Type"="application/json"),
                        httr::timeout(90))
      dat <- httr::content(r, as="parsed", type="application/json")
      if (isTRUE(dat$ok)) {
        census_data_loaded(FALSE)  # force reload
        census_refresh_rv(dat$message %||% "Refresh complete.")
      } else {
        census_refresh_rv(paste0("Error: ", dat$message %||% "Unknown"))
      }
    }, error=function(e) census_refresh_rv(paste0("Request failed: ", e$message)))
  })

  # ── Florida Rivers ────────────────────────────────────────────────────────

  FLOOD_CAT_COLORS <- c(
    Normal   = "#1a6bb5",
    Action   = "#d4a017",
    Minor    = "#e67e22",
    Moderate = "#c0460a",
    Major    = "#8b0000",
    Record   = "#6a0572",
    Unknown  = "#888888"
  )

  rv_refresh_trigger <- reactiveVal(0)
  rv_timer           <- reactiveTimer(120000)  # 2-minute auto-refresh

  rv_gauges_data <- reactive({
    rv_timer()
    rv_refresh_trigger()
    col <- tryCatch(
      mongo(collection = "fl_river_gauges", db = "weather_rss", url = MONGO_URI),
      error = function(e) NULL
    )
    if (is.null(col)) return(data.frame())
    tryCatch({
      d <- col$find('{}', fields = '{"_id":0}', sort = '{"flood_category":-1,"name":1}')
      col$disconnect()
      d
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL)
      data.frame()
    })
  })

  rv_latest_alert <- reactive({
    rv_timer()
    rv_refresh_trigger()
    col <- tryCatch(
      mongo(collection = "fl_river_alerts", db = "weather_rss", url = MONGO_URI),
      error = function(e) NULL
    )
    if (is.null(col)) return(NULL)
    tryCatch({
      d <- col$find('{}', fields = '{"_id":0}', sort = '{"generated_at":-1}', limit = 1L)
      col$disconnect()
      if (nrow(d) == 0) NULL else d[1, ]
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL)
      NULL
    })
  })

  observeEvent(input$btn_river_refresh, { rv_refresh_trigger(rv_refresh_trigger() + 1) })

  # ── Value boxes ─────────────────────────────────────────────────────────────
  output$rv_box_total <- renderValueBox({
    df <- rv_gauges_data()
    valueBox(nrow(df), "Gauges Monitored", icon = icon("gauge"), color = "blue")
  })

  output$rv_box_flood <- renderValueBox({
    df <- rv_gauges_data()
    if (nrow(df) == 0) { valueBox(0, "At Flood Stage", icon = icon("house-flood-water"), color = "green"); return() }
    n <- sum(df$flood_category %in% c("Action","Minor","Moderate","Major","Record"), na.rm = TRUE)
    color <- if (n == 0) "green" else if (n <= 2) "yellow" else "red"
    valueBox(n, "At Flood Stage", icon = icon("house-flood-water"), color = color)
  })

  output$rv_box_worst <- renderValueBox({
    df <- rv_gauges_data()
    order_map <- c(Normal=0,Unknown=0,Action=1,Minor=2,Moderate=3,Major=4,Record=5)
    if (nrow(df) == 0 || !"flood_category" %in% colnames(df)) {
      valueBox("Normal", "Worst Category", icon = icon("check-circle"), color = "green")
      return()
    }
    worst <- df$flood_category[which.max(order_map[df$flood_category])]
    worst <- if (is.na(worst) || length(worst)==0) "Normal" else worst
    color <- switch(worst, Action="yellow", Minor="orange", Moderate="red", Major="red", Record="purple", "green")
    valueBox(worst, "Worst Category", icon = icon("triangle-exclamation"), color = color)
  })

  output$rv_box_updated <- renderValueBox({
    df <- rv_gauges_data()
    if (nrow(df) == 0 || !"updated_at" %in% colnames(df)) {
      valueBox("—", "Last Update", icon = icon("clock"), color = "light-blue")
      return()
    }
    ts <- tryCatch(max(as.POSIXct(df$updated_at, tz="UTC"), na.rm=TRUE), error=function(e) NA)
    label <- if (is.na(ts)) "—" else format(ts, "%H:%M UTC")
    valueBox(label, "Last Update", icon = icon("clock"), color = "light-blue")
  })

  # ── Gauges DT ───────────────────────────────────────────────────────────────
  output$tbl_river_gauges <- renderDT({
    df <- rv_gauges_data()
    if (nrow(df) == 0) {
      return(datatable(data.frame(Message = "No river gauge data yet. Run: sudo bash systemd/install_rivers.sh"),
                       options = list(dom = "t"), rownames = FALSE))
    }
    keep_cols <- intersect(
      c("name","river","county","flood_category","current_stage_ft","action_stage_ft",
        "minor_stage_ft","stage_trend","wfo","lid"),
      colnames(df)
    )
    display <- df[, keep_cols, drop = FALSE]
    col_labels <- c(
      name = "Gauge Name", river = "River", county = "County",
      flood_category = "Status", current_stage_ft = "Stage (ft)",
      action_stage_ft = "Action (ft)", minor_stage_ft = "Minor (ft)",
      stage_trend = "Trend", wfo = "WFO", lid = "LID"
    )
    colnames(display) <- col_labels[colnames(display)]

    datatable(
      display,
      selection  = "single",
      rownames   = FALSE,
      extensions = "Buttons",
      options    = list(
        pageLength  = 20,
        scrollX     = TRUE,
        dom         = "Bfrtip",
        buttons     = list("csv"),
        columnDefs  = list(list(className = "dt-center", targets = "_all"))
      )
    ) %>%
      formatStyle(
        "Status",
        backgroundColor = styleEqual(
          names(FLOOD_CAT_COLORS),
          unname(FLOOD_CAT_COLORS)
        ),
        color = "white",
        fontWeight = "bold"
      )
  })

  # ── AI Summary ──────────────────────────────────────────────────────────────
  output$rv_ai_summary <- renderUI({
    alert <- rv_latest_alert()
    if (is.null(alert)) {
      return(tags$p(tags$em("No AI analysis yet. The agent runs hourly after the fetcher populates data."),
                    style = "color:#888; font-size:13px;"))
    }
    sev <- alert$severity %||% "None"
    sev_color <- switch(sev,
      Action="warning", Minor="warning", Moderate="danger", Major="danger", "success"
    )
    gen_at <- tryCatch(
      format(as.POSIXct(alert$generated_at, tz="UTC"), "%Y-%m-%d %H:%M UTC"),
      error = function(e) "unknown"
    )
    tags$div(
      tags$div(
        class = paste0("alert alert-", sev_color),
        style = "font-size:13px; margin-bottom:10px;",
        tags$strong(paste0("Severity: ", sev)),
        tags$br(),
        tags$span(style="font-size:11px;", paste0("Generated: ", gen_at))
      ),
      tags$p(alert$summary_text %||% "", style = "font-size:13px; line-height:1.6;"),
      if (!is.null(alert$flood_gauge_count) && alert$flood_gauge_count > 0)
        tags$p(tags$small(paste0(alert$flood_gauge_count, " gauge(s) at/above Action stage.")),
               style="color:#888;")
    )
  })

  # ── Agent run (admin) ────────────────────────────────────────────────────────
  rv_agent_status_rv <- reactiveVal("")
  output$rv_agent_status <- renderText({ rv_agent_status_rv() })

  observeEvent(input$btn_river_agent_run, {
    rv_agent_status_rv("Running agent...")
    tryCatch({
      r <- httr::POST(
        "http://localhost:5000/api/rivers/agent/run",
        httr::timeout(130)
      )
      if (httr::status_code(r) == 200) {
        rv_agent_status_rv("Agent complete. Refreshing...")
        rv_refresh_trigger(rv_refresh_trigger() + 1)
      } else {
        rv_agent_status_rv(paste0("Error: HTTP ", httr::status_code(r)))
      }
    }, error = function(e) rv_agent_status_rv(paste0("Error: ", e$message)))
  })

  # ── Trend chart ──────────────────────────────────────────────────────────────
  rv_selected_lid <- reactiveVal(NULL)

  observeEvent(input$tbl_river_gauges_rows_selected, {
    df <- rv_gauges_data()
    sel <- input$tbl_river_gauges_rows_selected
    if (!is.null(sel) && length(sel) > 0 && nrow(df) >= sel) {
      rv_selected_lid(df$lid[sel])
    }
  })

  output$rv_trend_title <- renderUI({
    lid <- rv_selected_lid()
    if (is.null(lid)) return(tags$span("Gauge Trend — select a row above"))
    df <- rv_gauges_data()
    name <- if (nrow(df) > 0 && "name" %in% colnames(df)) {
      nm <- df$name[df$lid == lid]
      if (length(nm) > 0) nm[1] else lid
    } else lid
    tags$span(paste0("24-Hour Trend — ", name))
  })

  rv_readings_data <- reactive({
    lid <- rv_selected_lid()
    if (is.null(lid)) return(data.frame())
    col <- tryCatch(
      mongo(collection = "fl_river_readings", db = "weather_rss", url = MONGO_URI),
      error = function(e) NULL
    )
    if (is.null(col)) return(data.frame())
    query <- sprintf('{"lid":"%s","fetched_at":{"$gte":{"$date":"%s"}}}',
                     lid, format(Sys.time() - 86400, "%Y-%m-%dT%H:%M:%SZ"))
    tryCatch({
      d <- col$find(query, fields = '{"_id":0,"gage_height_ft":1,"flood_category":1,"fetched_at":1}',
                    sort = '{"fetched_at":1}', limit = 200L)
      col$disconnect()
      d
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL)
      data.frame()
    })
  })

  output$rv_trend_chart <- renderPlotly({
    lid <- rv_selected_lid()
    if (is.null(lid)) {
      return(plotly_empty() %>% layout(title = "Select a gauge row to view trend"))
    }
    df <- rv_readings_data()
    if (nrow(df) == 0 || !"gage_height_ft" %in% colnames(df)) {
      return(plotly_empty() %>% layout(title = "No readings available for this gauge"))
    }
    df$ts <- as.POSIXct(df$fetched_at, tz = "UTC", format = "%Y-%m-%dT%H:%M:%SZ")
    df$ts[is.na(df$ts)] <- as.POSIXct(df$fetched_at[is.na(df$ts)], tz = "UTC")
    df <- df[!is.na(df$ts) & !is.na(df$gage_height_ft), ]
    if (nrow(df) == 0) return(plotly_empty() %>% layout(title = "No valid readings"))

    # Gauge metadata for flood stage lines
    gdf <- rv_gauges_data()
    action_ft <- moderate_ft <- NULL
    if (nrow(gdf) > 0 && "lid" %in% colnames(gdf)) {
      gr <- gdf[gdf$lid == lid, ]
      if (nrow(gr) > 0) {
        if ("action_stage_ft" %in% colnames(gr))   action_ft   <- gr$action_stage_ft[1]
        if ("moderate_stage_ft" %in% colnames(gr)) moderate_ft <- gr$moderate_stage_ft[1]
      }
    }

    p <- plot_ly(df, x = ~ts, y = ~gage_height_ft,
                 type = "scatter", mode = "lines+markers",
                 line = list(color = "#1a6bb5", width = 2),
                 marker = list(size = 5, color = "#1a6bb5"),
                 name = "Gage Height (ft)") %>%
      layout(
        xaxis = list(title = ""),
        yaxis = list(title = "Gage Height (ft)"),
        margin = list(l=50, r=20, t=20, b=40),
        hovermode = "x unified"
      )

    if (!is.null(action_ft) && !is.na(action_ft))
      p <- p %>% add_segments(
        x = ~min(ts), xend = ~max(ts),
        y = action_ft, yend = action_ft,
        line = list(color = "#d4a017", width = 1.5, dash = "dash"),
        name = paste0("Action Stage (", action_ft, " ft)"),
        inherit = FALSE
      )
    if (!is.null(moderate_ft) && !is.na(moderate_ft))
      p <- p %>% add_segments(
        x = ~min(ts), xend = ~max(ts),
        y = moderate_ft, yend = moderate_ft,
        line = list(color = "#c0460a", width = 1.5, dash = "dash"),
        name = paste0("Moderate Flood (", moderate_ft, " ft)"),
        inherit = FALSE
      )
    p
  })

  # ── User SMS / Role Management ───────────────────────────────────────────────
  user_sms_rv    <- reactiveVal(0)
  user_sms_edits <- reactiveVal(list())
  sms_roles_msg  <- reactiveVal("")
  output$sms_roles_status <- renderText({ sms_roles_msg() })

  output$user_sms_table <- renderDT({
    user_mgmt_rv()
    user_sms_rv()
    col <- tryCatch(
      mongo(collection = "users", db = "weather_rss", url = MONGO_URI),
      error = function(e) NULL)
    if (is.null(col)) return(datatable(data.frame(Message = "DB unavailable"), rownames = FALSE))
    tryCatch({
      u <- col$find("{}", fields = '{"username":1,"role":1,"sms_emergency_enabled":1,"phone":1,"profession":1,"_id":0}')
      col$disconnect()
      u$sms_emergency_enabled <- ifelse(is.na(u$sms_emergency_enabled) | is.null(u$sms_emergency_enabled),
                                        TRUE, as.logical(u$sms_emergency_enabled))
      colnames(u)[colnames(u) == "sms_emergency_enabled"] <- "SMS Enabled"
      datatable(u,
        editable = list(target = "cell", disable = list(columns = c(0, 3, 4))),
        options  = list(pageLength = 15, scrollX = TRUE),
        rownames = FALSE,
        caption  = "Edit Role (col 1) or SMS Enabled (col 2 — true/false). Click Save to persist.")
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL)
      datatable(data.frame(Message = conditionMessage(e)), rownames = FALSE)
    })
  })

  observeEvent(input$user_sms_table_cell_edit, {
    info <- input$user_sms_table_cell_edit
    cur  <- user_sms_edits()
    cur[[as.character(info$row)]] <- list(col = info$col, value = info$value)
    user_sms_edits(cur)
  })

  observeEvent(input$btn_save_sms_roles, {
    if (!isTRUE(auth_rv$role == "admin")) { sms_roles_msg("Access denied."); return() }
    edits <- user_sms_edits()
    if (length(edits) == 0) { sms_roles_msg("No changes to save."); return() }
    col <- tryCatch(
      mongo(collection = "users", db = "weather_rss", url = MONGO_URI),
      error = function(e) NULL)
    if (is.null(col)) { sms_roles_msg("DB unavailable."); return() }
    tryCatch({
      u <- col$find("{}", fields = '{"username":1,"role":1,"sms_emergency_enabled":1,"_id":0}')
      saved <- 0
      for (row_key in names(edits)) {
        row_idx   <- as.integer(row_key)
        ei        <- edits[[row_key]]
        if (row_idx < 1 || row_idx > nrow(u)) next
        uname     <- u$username[row_idx]
        if (ei$col == 1) {  # role
          col$update(sprintf('{"username":"%s"}', uname),
                     sprintf('{"$set":{"role":"%s"}}', gsub('"', '', as.character(ei$value))))
          saved <- saved + 1
        } else if (ei$col == 2) {  # SMS enabled
          enabled <- tolower(trimws(as.character(ei$value))) %in% c("true","1","yes","TRUE")
          col$update(sprintf('{"username":"%s"}', uname),
                     sprintf('{"$set":{"sms_emergency_enabled":%s}}', tolower(as.character(enabled))))
          saved <- saved + 1
        }
      }
      col$disconnect()
      user_sms_edits(list())
      user_sms_rv(user_sms_rv() + 1)
      sms_roles_msg(paste0("Saved ", saved, " change(s)."))
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL)
      sms_roles_msg(paste0("Error: ", conditionMessage(e)))
    })
  })

  # ── Emergency SMS To-Do List Editor ─────────────────────────────────────────
  todo_edit_msg <- reactiveVal("")
  output$todo_edit_status <- renderText({ todo_edit_msg() })

  observeEvent(input$btn_load_todos, {
    role <- input$todo_role
    if (is.null(role) || nchar(role) == 0) { todo_edit_msg("Select a role first."); return() }
    col <- tryCatch(
      mongo(collection = "emergency_roles_config", db = "weather_rss", url = MONGO_URI),
      error = function(e) NULL)
    if (is.null(col)) { todo_edit_msg("DB unavailable."); return() }
    tryCatch({
      for (phase in c("before","during","after")) {
        q <- sprintf('{"role":"%s","phase":"%s"}', gsub('"', '', role), phase)
        r <- col$find(q, fields = '{"todos":1,"_id":0}')
        todos <- if (nrow(r) > 0 && !is.null(r$todos)) {
          t <- r$todos[[1]]
          if (is.list(t)) paste(unlist(t), collapse = "\n") else paste(as.character(t), collapse = "\n")
        } else ""
        field_id <- paste0("todo_", phase)
        updateTextAreaInput(session, field_id, value = todos)
      }
      col$disconnect()
      todo_edit_msg(paste0("Loaded: ", role))
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL)
      todo_edit_msg(paste0("Error: ", conditionMessage(e)))
    })
  })

  observeEvent(input$btn_save_todos, {
    if (!isTRUE(auth_rv$role == "admin")) { todo_edit_msg("Access denied."); return() }
    role <- input$todo_role
    if (is.null(role) || nchar(role) == 0) { todo_edit_msg("Select a role first."); return() }
    col <- tryCatch(
      mongo(collection = "emergency_roles_config", db = "weather_rss", url = MONGO_URI),
      error = function(e) NULL)
    if (is.null(col)) { todo_edit_msg("DB unavailable."); return() }
    tryCatch({
      now_str <- format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
      for (phase in c("before","during","after")) {
        field_id <- paste0("todo_", phase)
        raw      <- input[[field_id]] %||% ""
        todos    <- Filter(nchar, trimws(strsplit(raw, "\n")[[1]]))
        id_key   <- paste0(role, "|", phase)
        col$upsert(
          sprintf('{"_id":"%s"}', gsub('"', '', id_key)),
          sprintf('{"$set":{"role":"%s","phase":"%s","todos":%s,"updated_at":"%s"}}',
                  gsub('"', '', role), phase,
                  jsonlite::toJSON(todos, auto_unbox = FALSE),
                  now_str)
        )
      }
      col$disconnect()
      todo_edit_msg(paste0("Saved to-do lists for: ", role))
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL)
      todo_edit_msg(paste0("Error: ", conditionMessage(e)))
    })
  })

  # ── SMS Blast ────────────────────────────────────────────────────────────────
  sms_blast_msg <- reactiveVal("")
  output$sms_blast_status  <- renderText({ sms_blast_msg() })
  output$sms_blast_preview <- renderText({
    req(input$btn_preview_sms)
    isolate({
      role  <- input$sms_blast_role %||% "__all__"
      phase <- input$sms_blast_phase %||% "before"
      col <- tryCatch(
        mongo(collection = "emergency_roles_config", db = "weather_rss", url = MONGO_URI),
        error = function(e) NULL)
      todos <- tryCatch({
        if (is.null(col) || role == "__all__") return("(Select a specific role to preview)")
        q <- sprintf('{"role":"%s","phase":"%s"}', gsub('"', '', role), phase)
        r <- col$find(q, fields = '{"todos":1,"_id":0}')
        col$disconnect()
        if (nrow(r) == 0 || is.null(r$todos)) character(0)
        else { t <- r$todos[[1]]; if (is.list(t)) unlist(t) else as.character(t) }
      }, error = function(e) { tryCatch(col$disconnect(), error=function(e2) NULL); character(0) })
      phase_lbl <- c(before="BEFORE EVENT",during="DURING EVENT",after="AFTER EVENT")[[phase]]
      header <- paste0("FPREN EMERGENCY — ", phase_lbl, "\nActions for ", role, ":\n")
      if (length(todos) == 0) return(paste0(header, "(No checklist defined for this role/phase)\n—FPREN"))
      items  <- paste(seq_along(todos), todos, sep=". ", collapse="\n")
      paste0(header, items, "\nReply STOP to opt out. —FPREN")
    })
  }) %>% bindEvent(input$btn_preview_sms)

  observeEvent(input$btn_send_sms_blast, {
    if (!isTRUE(auth_rv$role == "admin")) { sms_blast_msg("Access denied."); return() }
    role  <- input$sms_blast_role  %||% "__all__"
    phase <- input$sms_blast_phase %||% "before"
    sms_blast_msg("Sending SMS...")
    col <- tryCatch(
      mongo(collection = "users", db = "weather_rss", url = MONGO_URI),
      error = function(e) NULL)
    if (is.null(col)) { sms_blast_msg("DB unavailable."); return() }
    phones <- tryCatch({
      q <- if (role == "__all__") '{"sms_emergency_enabled":true}' else
        sprintf('{"sms_emergency_enabled":true,"profession":"%s"}', gsub('"','',role))
      u <- col$find(q, fields = '{"phone":1,"_id":0}')
      col$disconnect()
      phones <- u$phone[!is.na(u$phone) & nchar(trimws(u$phone)) > 0]
      trimws(phones)
    }, error = function(e) { tryCatch(col$disconnect(), error=function(e2) NULL); character(0) })
    if (length(phones) == 0) { sms_blast_msg("No SMS-enabled users with phone numbers found."); return() }
    phones_csv <- paste(phones, collapse = ",")
    result <- tryCatch(
      system2("python3",
              args = c(
                "/home/ufuser/Fpren-main/weather_rss/emergency_sms.py",
                "--phones", shQuote(phones_csv),
                "--role",   shQuote(if (role == "__all__") "General" else role),
                "--phase",  shQuote(phase),
                "--mongo-uri", shQuote(MONGO_URI)),
              stdout = TRUE, stderr = TRUE, timeout = 120),
      error = function(e) paste("System error:", conditionMessage(e))
    )
    sms_blast_msg(paste(result, collapse = "\n"))
  })

  # ── Test SMS ──────────────────────────────────────────────────────────────────
  test_sms_status_rv <- reactiveVal("")
  output$test_sms_status <- renderText({ test_sms_status_rv() })

  observeEvent(input$btn_send_test_sms, {
    if (!isTRUE(auth_rv$role == "admin")) {
      test_sms_status_rv("Admin role required."); return()
    }
    phone <- trimws(input$test_sms_phone %||% "")
    msg   <- trimws(input$test_sms_msg   %||% "")
    if (nchar(phone) == 0) { test_sms_status_rv("Phone number required."); return() }
    if (nchar(msg)   == 0) { test_sms_status_rv("Message cannot be empty."); return() }
    test_sms_status_rv(paste0("Sending to ", phone, " ..."))
    ok <- send_twilio_sms(phone, msg)
    test_sms_status_rv(if (ok)
      paste0("Sent successfully to ", phone)
    else
      paste0("Failed — check Twilio credentials in Stream Alerts tab, then retry."))
  })

  # ── Accessibility / Firewall Report ──────────────────────────────────────────
  access_report_status_rv <- reactiveVal("")
  access_report_path_rv   <- reactiveVal(NULL)
  output$access_report_status <- renderText({ access_report_status_rv() })

  output$access_report_download_ui <- renderUI({
    path <- access_report_path_rv()
    if (is.null(path) || !file.exists(path)) return(NULL)
    fname <- basename(path)
    tags$div(style = "margin-top:8px;",
      icon("file-pdf"), " Report ready: ",
      tags$a(href = paste0("/fpren/reports/output/", fname), target = "_blank",
             tags$strong(fname)),
      tags$small(style = "color:#888; margin-left:8px;",
                 paste0("(", round(file.size(path)/1024, 1), " KB)"))
    )
  })

  observeEvent(input$btn_gen_access_report, {
    if (!isTRUE(auth_rv$role == "admin")) {
      access_report_status_rv("Admin role required."); return()
    }
    access_report_status_rv("Running connectivity checks (may take 30-60 s)...")
    access_report_path_rv(NULL)
    ts      <- format(Sys.time(), "%Y%m%d_%H%M%S")
    out_dir <- "/home/ufuser/Fpren-main/reports/output"
    if (!dir.exists(out_dir)) dir.create(out_dir, recursive = TRUE)
    out_pdf <- file.path(out_dir, paste0("fpren_accessibility_", ts, ".pdf"))
    result <- tryCatch(
      rmarkdown::render(
        "/home/ufuser/Fpren-main/reports/fpren_accessibility_report.Rmd",
        output_file = out_pdf,
        params = list(
          mongo_uri      = MONGO_URI,
          checker_script = "/home/ufuser/Fpren-main/scripts/check_fpren_access.py",
          python_bin     = "/home/ufuser/Fpren-main/venv/bin/python3"
        ),
        envir  = new.env(parent = globalenv()),
        quiet  = TRUE
      ),
      error = function(e) { access_report_status_rv(paste0("Render error: ", conditionMessage(e))); NULL }
    )
    if (!is.null(result) && file.exists(out_pdf)) {
      access_report_path_rv(out_pdf)
      access_report_status_rv(paste0("Report generated: ", basename(out_pdf)))
      if (isTRUE(input$access_report_email)) {
        send_fpren_email(auth_rv$email,
          paste0("[FPREN] Connectivity & Firewall Report — ", format(Sys.time(), "%Y-%m-%d %H:%M")),
          paste0("<p>Attached: FPREN Connectivity & Firewall Diagnostics report generated ",
                 format(Sys.time(), "%B %d, %Y at %I:%M %p %Z"), ".</p>"),
          attachment_path = out_pdf)
        access_report_status_rv(paste0("Report generated and emailed to ", auth_rv$email))
      }
    }
  })

  # ── Group Invite Toggle ───────────────────────────────────────────────────────
  group_invite_rv     <- reactiveVal(0)
  group_invite_msg_rv <- reactiveVal("")
  output$group_invite_status <- renderText({ group_invite_msg_rv() })

  output$group_invite_toggle_ui <- renderUI({
    group_invite_rv()
    uname <- auth_rv$username %||% ""
    if (nchar(uname) == 0) return(NULL)
    col <- tryCatch(mongo("users", "weather_rss", url = MONGO_URI),
                    error = function(e) NULL)
    if (is.null(col)) return(p("DB unavailable"))
    enabled <- tryCatch({
      r <- col$find(sprintf('{"username":"%s"}', uname),
                    fields = '{"group_invites_enabled":1,"_id":0}')
      col$disconnect()
      if (nrow(r) > 0 && !is.null(r$group_invites_enabled) &&
          !is.na(r$group_invites_enabled))
        isTRUE(r$group_invites_enabled)
      else TRUE   # default: ON
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL); TRUE
    })
    tagList(
      div(style = "margin-bottom:10px;",
        tags$strong("Send invites when I add users: "),
        tags$span(
          style = if (enabled) "color:#27ae60;font-weight:600;" else "color:#e74c3c;font-weight:600;",
          if (enabled) "ON" else "OFF"
        )
      ),
      if (enabled)
        actionButton("btn_group_invite_disable", "Disable Group Invites",
                     class = "btn-warning btn-sm", icon = icon("user-slash"))
      else
        actionButton("btn_group_invite_enable", "Enable Group Invites",
                     class = "btn-success btn-sm", icon = icon("user-check"))
    )
  })

  observeEvent(input$btn_group_invite_enable, {
    if (!isTRUE(auth_rv$role == "admin")) return()
    uname <- auth_rv$username %||% ""
    col <- tryCatch(mongo("users", "weather_rss", url = MONGO_URI),
                    error = function(e) NULL)
    if (is.null(col)) { group_invite_msg_rv("DB unavailable."); return() }
    tryCatch({
      col$update(sprintf('{"username":"%s"}', uname),
                 '{"$set":{"group_invites_enabled":true}}')
      col$disconnect()
      group_invite_msg_rv("Group invites ENABLED — new users will receive invite email and SMS.")
      group_invite_rv(group_invite_rv() + 1)
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL)
      group_invite_msg_rv(paste("Error:", conditionMessage(e)))
    })
  })

  observeEvent(input$btn_group_invite_disable, {
    if (!isTRUE(auth_rv$role == "admin")) return()
    uname <- auth_rv$username %||% ""
    col <- tryCatch(mongo("users", "weather_rss", url = MONGO_URI),
                    error = function(e) NULL)
    if (is.null(col)) { group_invite_msg_rv("DB unavailable."); return() }
    tryCatch({
      col$update(sprintf('{"username":"%s"}', uname),
                 '{"$set":{"group_invites_enabled":false}}')
      col$disconnect()
      group_invite_msg_rv("Group invites DISABLED — users you add will NOT receive invite email or SMS.")
      group_invite_rv(group_invite_rv() + 1)
    }, error = function(e) {
      tryCatch(col$disconnect(), error = function(e2) NULL)
      group_invite_msg_rv(paste("Error:", conditionMessage(e)))
    })
  })

}


shinyApp(ui, server)
