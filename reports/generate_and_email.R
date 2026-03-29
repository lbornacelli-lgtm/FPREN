#!/usr/bin/env Rscript
# FPREN Alert Report — render PDF and email to recipient
#
# Usage:
#   Rscript generate_and_email.R [days_back] [zone_label] [severity_filter] [event_filter] [date_from] [date_to] [send_email]
#
# Examples:
#   Rscript generate_and_email.R                                     # 7-day All Florida, email
#   Rscript generate_and_email.R 30                                  # 30-day report
#   Rscript generate_and_email.R 7 "All Florida" "Extreme,Severe"   # filtered by severity
#   Rscript generate_and_email.R 0 "All Florida" "all" "all" "2026-03-01" "2026-03-28"  # date range
#
# Environment / config:
#   SMTP config is read from /home/ufuser/Fpren-main/weather_rss/config/smtp_config.json
#   MONGO_URI defaults to mongodb://localhost:27017/

suppressPackageStartupMessages({
  library(rmarkdown)
  library(emayili)
  library(lubridate)
  library(jsonlite)
})

`%||%` <- function(a, b) if (!is.null(a) && length(a) > 0 && nchar(as.character(a)) > 0) a else b

# ── Parse args ────────────────────────────────────────────────────────────────
args             <- commandArgs(trailingOnly = TRUE)
days_back        <- as.integer(args[1] %||% 7)
if (is.na(days_back)) days_back <- 7
zone_label       <- as.character(args[2] %||% "All Florida")
severity_filter  <- as.character(args[3] %||% "all")
event_filter     <- as.character(args[4] %||% "all")
date_from        <- as.character(args[5] %||% "")
date_to          <- as.character(args[6] %||% "")
send_email_flag  <- tolower(as.character(args[7] %||% "true"))
send_email       <- send_email_flag != "false"

mongo_uri <- Sys.getenv("MONGO_URI", "mongodb://localhost:27017/")

# ── SMTP config ───────────────────────────────────────────────────────────────
smtp_cfg_path <- "/home/ufuser/Fpren-main/weather_rss/config/smtp_config.json"
smtp_cfg <- tryCatch(fromJSON(smtp_cfg_path), error = function(e) list())

smtp_host <- smtp_cfg$smtp_host %||% "smtp.ufl.edu"
smtp_port <- as.integer(smtp_cfg$smtp_port %||% 25)
mail_from <- smtp_cfg$mail_from %||% "lawrence.bornace@ufl.edu"
mail_to   <- smtp_cfg$mail_to   %||% "lawrence.bornace@ufl.edu"

# ── Output paths ──────────────────────────────────────────────────────────────
report_dir <- "/home/ufuser/Fpren-main/reports/output"
dir.create(report_dir, showWarnings = FALSE, recursive = TRUE)

timestamp   <- format(Sys.time(), "%Y%m%d_%H%M%S")
sev_tag     <- if (severity_filter != "all") paste0("_", gsub(",","", severity_filter)) else ""
output_file <- file.path(report_dir,
                          paste0("fpren_report_", timestamp, sev_tag, ".pdf"))
rmd_path    <- "/home/ufuser/Fpren-main/reports/fpren_alert_report.Rmd"

# ── Build period label ────────────────────────────────────────────────────────
if (nchar(date_from) > 0 && nchar(date_to) > 0) {
  period_label <- paste0(date_from, " to ", date_to)
} else {
  period_label <- paste0(
    format(Sys.time() - days(days_back), "%b %d"),
    " - ", format(Sys.time(), "%b %d, %Y")
  )
}

# ── Render PDF ────────────────────────────────────────────────────────────────
cat(sprintf("[FPREN Report] Rendering: %s | zone=%s | sev=%s | events=%s\n",
            period_label, zone_label, severity_filter, event_filter))

tryCatch({
  rmarkdown::render(
    input       = rmd_path,
    output_file = output_file,
    params      = list(
      days_back       = days_back,
      zone_label      = zone_label,
      mongo_uri       = mongo_uri,
      severity_filter = severity_filter,
      event_filter    = event_filter,
      date_from       = date_from,
      date_to         = date_to
    ),
    quiet = TRUE
  )
  cat(sprintf("[FPREN Report] PDF saved: %s\n", output_file))
}, error = function(e) {
  cat(sprintf("[FPREN Report] ERROR rendering PDF: %s\n", conditionMessage(e)))
  quit(status = 1)
})

if (!file.exists(output_file)) {
  cat("[FPREN Report] ERROR: PDF not found after render.\n")
  quit(status = 1)
}

# ── Email PDF ─────────────────────────────────────────────────────────────────
if (!send_email) {
  cat("[FPREN Report] Email skipped (send_email=false).\n")
  cat("[FPREN Report] Done.\n")
  cat(sprintf("OUTPUT_FILE:%s\n", output_file))
  quit(status = 0)
}

sev_line   <- if (severity_filter != "all") paste0("Severity: ", severity_filter, "\n") else ""
event_line <- if (event_filter != "all")    paste0("Events:   ", event_filter, "\n")    else ""

subject <- sprintf("FPREN Alert Report - %s (%s)", zone_label, period_label)

cat(sprintf("[FPREN Report] Emailing to %s via %s:%d...\n", mail_to, smtp_host, smtp_port))

tryCatch({
  email <- envelope() %>%
    from(mail_from) %>%
    to(mail_to) %>%
    subject(subject) %>%
    text(paste0(
      "FPREN Weather Alert Report\n",
      "==========================\n\n",
      "Period:    ", period_label, "\n",
      "Zone:      ", zone_label, "\n",
      sev_line, event_line,
      "Generated: ", format(Sys.time(), "%Y-%m-%d %H:%M:%S UTC"), "\n\n",
      "Please find the full PDF report attached.\n\n",
      "-- FPREN Automated Reporting System\n",
      "   Florida Public Radio Emergency Network\n"
    )) %>%
    attachment(output_file)

  smtp_srv <- server(host = smtp_host, port = smtp_port, reuse = FALSE)
  smtp_srv(email, verbose = FALSE)
  cat(sprintf("[FPREN Report] Email sent to %s\n", mail_to))
}, error = function(e) {
  cat(sprintf("[FPREN Report] ERROR sending email: %s\n", conditionMessage(e)))
  cat("[FPREN Report] PDF is still saved at:", output_file, "\n")
  quit(status = 2)
})

cat("[FPREN Report] Done.\n")
cat(sprintf("OUTPUT_FILE:%s\n", output_file))
