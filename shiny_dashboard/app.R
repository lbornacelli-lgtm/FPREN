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
      menuItem("Alachua County",  tabName = "alachua",   icon = icon("map-marker-alt")),
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
          valueBoxOutput("box_alachua_alerts",  width = 3),
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

      # ── Alachua County ──────────────────────────────────────────────────────
      tabItem(tabName = "alachua",
        fluidRow(
          valueBoxOutput("box_alachua_active",  width = 4),
          valueBoxOutput("box_alachua_extreme", width = 4),
          valueBoxOutput("box_alachua_wavs",    width = 4)
        ),
        fluidRow(
          box(title = "Alachua County Alerts", width = 12, status = "warning",
              solidHeader = TRUE, DTOutput("tbl_alachua"))
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
        "severity":1,"area_desc":1,"source":1,"sent":1,
        "alachua_county":1,"_id":0}')
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

  output$box_alachua_alerts <- renderValueBox({
    df <- alerts_data()
    n  <- if (nrow(df) == 0 || !"alachua_county" %in% names(df)) 0
          else sum(df$alachua_county == TRUE, na.rm = TRUE)
    valueBox(n, "Alachua Alerts", icon = icon("map-marker-alt"),
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
                                  "source","alachua_county","sent")))
    datatable(df, options = list(pageLength = 10, scrollX = TRUE),
              rownames = FALSE) %>%
      formatStyle("severity",
        backgroundColor = styleEqual(
          c("Extreme","Severe","Moderate"),
          c("#f56954",  "#f39c12", "#f0ad4e")))
  })

  observeEvent(input$btn_refresh_alerts, { alerts_data() })

  # ── Alachua tab ─────────────────────────────────────────────────────────────

  alachua_df <- reactive({
    df <- alerts_data()
    if (nrow(df) == 0 || !"alachua_county" %in% names(df)) return(data.frame())
    df %>% filter(alachua_county == TRUE)
  })

  output$box_alachua_active <- renderValueBox({
    valueBox(nrow(alachua_df()), "Active Alachua Alerts",
             icon = icon("exclamation-circle"),
             color = if (nrow(alachua_df()) > 0) "red" else "green")
  })

  output$box_alachua_extreme <- renderValueBox({
    df <- alachua_df()
    n  <- if (nrow(df) == 0 || !"severity" %in% names(df)) 0
          else sum(tolower(df$severity) %in% c("extreme","severe"), na.rm = TRUE)
    valueBox(n, "Extreme/Severe", icon = icon("bolt"),
             color = if (n > 0) "red" else "green")
  })

  output$box_alachua_wavs <- renderValueBox({
    df <- wav_data()
    # Count WAVs for all_florida or north_florida zones (Alachua is north FL)
    n  <- if (nrow(df) == 0) 0
          else nrow(df %>% filter(zone %in% c("all_florida","north_florida")))
    valueBox(n, "Zone Audio Files", icon = icon("file-audio"), color = "blue")
  })

  output$tbl_alachua <- renderDT({
    df <- alachua_df()
    if (nrow(df) == 0)
      return(datatable(data.frame(Message = "No active Alachua County alerts")))
    df <- df %>% select(any_of(c("event","severity","headline","area_desc","sent","source")))
    datatable(df, options = list(pageLength = 10, scrollX = TRUE), rownames = FALSE)
  })

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
    valueBox(nrow(airport_data()), "Airports Monitored",
             icon = icon("globe"), color = "blue")
  })

  output$tbl_airports <- renderDT({
    df <- airport_data()
    if (nrow(df) == 0) return(datatable(data.frame(Message = "No airport data")))
    df <- df %>%
      mutate(status = ifelse(has_delay == TRUE, "DELAYED", "Normal")) %>%
      select(any_of(c("icao","name","state","status","fetched_at"))) %>%
      arrange(desc(status))
    datatable(df, options = list(pageLength = 20, scrollX = TRUE),
              rownames = FALSE) %>%
      formatStyle("status",
        color = styleEqual(c("DELAYED","Normal"), c("red","green")),
        fontWeight = styleEqual("DELAYED", "bold"))
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
