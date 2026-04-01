library(shiny)
library(shinydashboard)
library(mongolite)
library(DT)
library(dplyr)
library(lubridate)
library(rmarkdown)
library(httr)
library(jsonlite)

`%||%` <- function(a, b) if (!is.null(a) && nchar(a) > 0) a else b

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
ui <- dashboardPage(
  skin = "blue",

  dashboardHeader(title = "FPREN Weather Station"),

  dashboardSidebar(
    sidebarMenu(
      menuItem("Overview",        tabName = "overview",  icon = icon("tachometer-alt")),
      menuItem("FL Alerts",       tabName = "alerts",    icon = icon("exclamation-triangle")),
      menuItem("County Alerts",    tabName = "county_alerts", icon = icon("map-marker-alt")),
      menuItem("Airport Delays",  tabName = "airports",  icon = icon("plane")),
      menuItem("Station Health",  tabName = "health",    icon = icon("heartbeat")),
      menuItem("Feed Status",     tabName = "feeds",     icon = icon("rss")),
      menuItem("Reports",         tabName = "reports",       icon = icon("file-pdf")),
      menuItem("Stream Alerts",   tabName = "stream_alerts", icon = icon("bell")),
      menuItem("Config",          tabName = "config",        icon = icon("cog")),
      menuItem("Upload Content",   tabName = "upload",        icon = icon("upload")),
      menuItem("Zones",            tabName = "zones",         icon = icon("map"))
    )
  ),

  dashboardBody(
    tags$head(tags$style(HTML("
      .content-wrapper { background-color: #f4f6f9; }
      .small-box .icon { font-size: 60px; }
      .alert-extreme { background-color: #f56954 !important; color: white !important; }
      .alert-severe  { background-color: #f39c12 !important; color: white !important; }
    "))),

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
                    selected = "-- Select a county --")
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
              textInput("cfg_mail_from",  "Mail From Address", value = ""),
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
      tabItem(tabName = "zones",
        fluidRow(
          box(title = "Zone Definitions", width = 12, status = "info",
              solidHeader = TRUE,
              DT::dataTableOutput("zones_table")
          )
        ),
        fluidRow(
          box(title = "User Management", width = 12, status = "warning",
              solidHeader = TRUE,
              DT::dataTableOutput("users_table"),
              hr(),
              h5("Add New User"),
              fluidRow(
                column(3, textInput("new_user_name", "Username", value = "")),
                column(3, passwordInput("new_user_pass", "Password", value = "")),
                column(3, selectInput("new_user_role", "Role",
                  choices = c("admin","operator","viewer"), selected = "viewer")),
                column(3, br(), actionButton("btn_add_user", "Add User",
                  class = "btn-success", icon = icon("user-plus")))
              ),
              verbatimTextOutput("user_mgmt_status")
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
        )
      )
    )
  )
)

# ── Server ────────────────────────────────────────────────────────────────────
server <- function(input, output, session) {

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

  # ── County Alerts tab ────────────────────────────────────────────────────────

  ca_selected_county  <- reactiveVal(NULL)
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
      rmarkdown::render(
        input       = "/home/ufuser/Fpren-main/reports/county_alerts_report.Rmd",
        output_file = output_file,
        params      = list(county_name = county,
                           date        = format(Sys.Date(), "%Y-%m-%d"),
                           mongo_uri   = MONGO_URI),
        quiet = TRUE
      )
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
    result <- tryCatch({
      system2("/usr/bin/python3",
              args = c("/home/ufuser/Fpren-main/scripts/stream_notify.py", "reboot"),
              stdout = TRUE, stderr = TRUE, wait = TRUE)
      "Test email sent via notify script."
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
        rmarkdown::render(
          input       = "/home/ufuser/Fpren-main/reports/fpren_alert_report.Rmd",
          output_file = output_file,
          params      = list(days_back  = days,
                             zone_label = zone,
                             mongo_uri  = MONGO_URI),
          quiet = TRUE
        )
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

  # User Management
  user_mgmt_msg <- reactiveVal("")
  users_col <- get_col("users")
  output$users_table <- DT::renderDataTable({
    input$btn_add_user
    tryCatch({
      u <- users_col$find("{}", fields = '{"password":0,"_id":0}')
      if (nrow(u) == 0) return(data.frame(Message="No users found"))
      u
    }, error = function(e) data.frame(Error=conditionMessage(e)))
  }, options=list(pageLength=10), rownames=FALSE)
  observeEvent(input$btn_add_user, {
    req(input$new_user_name, input$new_user_pass)
    tryCatch({
      users_col$insert(data.frame(username=trimws(input$new_user_name),
        password=bcrypt::hashpw(input$new_user_pass),
        role=input$new_user_role, active=TRUE, stringsAsFactors=FALSE))
      user_mgmt_msg(paste("User", input$new_user_name, "created."))
      updateTextInput(session, "new_user_name", value="")
      updateTextInput(session, "new_user_pass", value="")
    }, error=function(e) user_mgmt_msg(paste("Error:", conditionMessage(e))))
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

  # Zones
  zones_col <- get_col("zone_definitions")
  output$zones_table <- DT::renderDataTable({
    tryCatch({
      z <- zones_col$find("{}", fields='{"zone_id":1,"display_name":1,"catch_all":1,"_id":0}')
      if (nrow(z)==0) return(data.frame(Message="No zones found"))
      z
    }, error=function(e) data.frame(Error=conditionMessage(e)))
  }, options=list(pageLength=15), rownames=FALSE)

}

shinyApp(ui, server)
