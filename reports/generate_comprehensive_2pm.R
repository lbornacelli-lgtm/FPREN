#!/usr/bin/env Rscript
# FPREN Comprehensive 2PM Report Generator
#
# Runs daily at 2:00 PM ET via fpren-comprehensive-2pm.timer
# Generates:
#   1. Daily alert summary report (all Florida, 7 days)
#   2. Weather trends PDF for all 16 FL cities (last 7 days)
#   3. Business Continuity Plan for every user asset in MongoDB
#
# At the end, emails a summary to the configured SMTP recipient.
#
# Usage:
#   Rscript generate_comprehensive_2pm.R

suppressPackageStartupMessages({
  library(rmarkdown)
  library(mongolite)
  library(jsonlite)
  library(emayili)
  library(lubridate)
  library(withr)
})

`%||%` <- function(a, b) if (!is.null(a) && length(a) > 0 && !is.na(a[1]) &&
                              nchar(as.character(a[1])) > 0) a else b

ts      <- function() format(Sys.time(), "[%Y-%m-%d %H:%M:%S]")
log_msg <- function(...) cat(ts(), ..., "\n", sep = " ")

MONGO_URI    <- Sys.getenv("MONGO_URI", "mongodb://localhost:27017/")
REPORT_DIR   <- "/home/ufuser/Fpren-main/reports/output"
REPORTS_BASE <- "/home/ufuser/Fpren-main/reports"
SMTP_CFG     <- "/home/ufuser/Fpren-main/weather_rss/config/smtp_config.json"

dir.create(REPORT_DIR, showWarnings = FALSE, recursive = TRUE)

smtp_cfg  <- tryCatch(fromJSON(SMTP_CFG), error = function(e) list())
smtp_host <- smtp_cfg$smtp_host %||% "smtp.ufl.edu"
smtp_port <- as.integer(smtp_cfg$smtp_port %||% 25)
mail_from <- smtp_cfg$mail_from %||% "lawrence.bornace@ufl.edu"
mail_to   <- smtp_cfg$mail_to   %||% "lawrence.bornace@ufl.edu"

generated <- list()
failed    <- list()

WX_CITIES <- data.frame(
  icao = c("KJAX","KTLH","KGNV","KOCF","KMCO","KDAB",
           "KTPA","KSPG","KSRQ","KRSW","KMIA","KFLL",
           "KPBI","KEYW","KPNS","KECP"),
  city = c("Jacksonville","Tallahassee","Gainesville","Ocala","Orlando","Daytona Beach",
           "Tampa","St. Petersburg","Sarasota","Fort Myers","Miami","Fort Lauderdale",
           "West Palm Beach","Key West","Pensacola","Panama City"),
  stringsAsFactors = FALSE
)

render_report <- function(rmd, output_file, params_list, label) {
  tryCatch({
    log_msg("Rendering:", label)
    withr::with_dir(tempdir(), rmarkdown::render(
      input             = rmd,
      output_file       = output_file,
      intermediates_dir = tempdir(),
      params            = params_list,
      quiet             = TRUE
    ))
    if (file.exists(output_file)) {
      log_msg("OK:", basename(output_file))
      return(basename(output_file))
    }
    log_msg("WARN: file missing after render:", label)
    return(NULL)
  }, error = function(e) {
    log_msg("ERROR:", label, "--", conditionMessage(e))
    return(NULL)
  })
}

stamp <- format(Sys.time(), "%Y%m%d_%H%M")

# ── 1. Alert Summary ─────────────────────────────────────────────────────────
log_msg("=== 1. Alert Summary Report ===")
alert_out <- file.path(REPORT_DIR, paste0("fpren_alert_report_2pm_", stamp, ".pdf"))
result <- render_report(
  file.path(REPORTS_BASE, "fpren_alert_report.Rmd"), alert_out,
  list(days_back = 7, zone_label = "All Florida", mongo_uri = MONGO_URI,
       severity_filter = "all", event_filter = "all", date_from = "", date_to = ""),
  "Alert Summary 7d")
if (!is.null(result)) generated <- c(generated, list(list(label="Alert Summary", file=result)))
else                  failed    <- c(failed,    "Alert Summary")

# ── 2. Weather Trends — 16 cities ────────────────────────────────────────────
log_msg("=== 2. Weather Trends (", nrow(WX_CITIES), "cities) ===")
wx_rmd   <- file.path(REPORTS_BASE, "weather_trends_report.Rmd")
start_d  <- as.character(Sys.Date() - 7)
end_d    <- as.character(Sys.Date())

for (i in seq_len(nrow(WX_CITIES))) {
  icao  <- WX_CITIES$icao[i]
  city  <- WX_CITIES$city[i]
  safe  <- gsub("[^A-Za-z0-9]", "_", city)
  out   <- file.path(REPORT_DIR, paste0("weather_trends_", safe, "_2pm_", stamp, ".pdf"))
  r     <- render_report(wx_rmd, out,
             list(icao=icao, city_name=city, start_date=start_d,
                  end_date=end_d, mongo_uri=MONGO_URI),
             paste0("WX Trends - ", city))
  if (!is.null(r)) generated <- c(generated, list(list(label=paste0("WX: ",city), file=r)))
  else             failed    <- c(failed, paste0("WX: ", city))
}

# ── 3. Business Continuity Plans ─────────────────────────────────────────────
log_msg("=== 3. Business Continuity Plans ===")
bcp_rmd   <- file.path(REPORTS_BASE, "business_continuity_report.Rmd")
users_col <- tryCatch(mongo(collection="users", db="weather_rss", url=MONGO_URI), error=function(e) NULL)

if (is.null(users_col)) {
  log_msg("ERROR: Cannot connect to MongoDB for user assets")
  failed <- c(failed, "BCP: MongoDB unavailable")
} else {
  users_with_assets <- tryCatch({
    r <- users_col$find('{"assets":{"$exists":true,"$not":{"$size":0}}}',
                        fields='{"username":1,"assets":1,"_id":0}')
    users_col$disconnect()
    r
  }, error = function(e) {
    tryCatch(users_col$disconnect(), error=function(e2) NULL)
    log_msg("ERROR querying assets:", conditionMessage(e))
    data.frame()
  })

  if (nrow(users_with_assets) == 0) {
    log_msg("No users with assets — skipping BCP")
  } else {
    log_msg("Found", nrow(users_with_assets), "users with assets")
    for (u_idx in seq_len(nrow(users_with_assets))) {
      uname  <- users_with_assets$username[u_idx]
      assets <- users_with_assets$assets[[u_idx]]
      if (is.null(assets) || length(assets) == 0) next

      is_df    <- is.data.frame(assets)
      n_assets <- if (is_df) nrow(assets) else length(assets)

      gf <- function(asset, field) {
        v <- tryCatch(as.character(asset[[field]]), error=function(e) "")
        if (is.null(v) || length(v)==0 || is.na(v[1])) "" else v[1]
      }

      for (a_idx in seq_len(n_assets)) {
        asset  <- if (is_df) assets[a_idx, ] else assets[[a_idx]]
        aname  <- gf(asset, "asset_name") %||% paste0("Asset_", a_idx)
        safe_a <- gsub("[^A-Za-z0-9]", "_", aname)
        out    <- file.path(REPORT_DIR,
                    paste0("bcp_", uname, "_", safe_a, "_", stamp, ".pdf"))
        lat <- tryCatch(as.numeric(gf(asset,"lat")), error=function(e) 29.65)
        lon <- tryCatch(as.numeric(gf(asset,"lon")), error=function(e) -82.33)
        if (is.na(lat)) lat <- 29.65
        if (is.na(lon)) lon <- -82.33

        r <- render_report(bcp_rmd, out,
               list(username=uname, asset_name=aname,
                    address=gf(asset,"address"),
                    lat=lat, lon=lon,
                    zip=gf(asset,"zip"),
                    city=gf(asset,"city") %||% "Unknown",
                    nearest_airport_icao=gf(asset,"nearest_airport_icao") %||% "KGNV",
                    nearest_airport_name=gf(asset,"nearest_airport_name") %||% "Gainesville Regional",
                    asset_type=gf(asset,"asset_type") %||% "Facility",
                    notes=gf(asset,"notes"),
                    mongo_uri=MONGO_URI, days_back=30),
               paste0("BCP - ", uname, "/", aname))
        if (!is.null(r))
          generated <- c(generated, list(list(label=paste0("BCP: ",uname,"/",aname), file=r)))
        else
          failed <- c(failed, paste0("BCP: ",uname,"/",aname))
      }
    }
  }
}

# ── 4. Summary email ─────────────────────────────────────────────────────────
log_msg("=== 4. Summary email ===")
n_ok   <- length(generated)
n_fail <- length(failed)
gen_lines  <- if (n_ok>0)   paste0(sapply(generated, function(x) paste0("  OK   ",x$label," -> ",x$file)), collapse="\n") else "  (none)"
fail_lines <- if (n_fail>0) paste0(sapply(failed,    function(x) paste0("  FAIL ",x)),                    collapse="\n") else "  (none)"

tryCatch({
  em <- envelope() %>%
    from(mail_from) %>% to(mail_to) %>%
    subject(paste0("FPREN 2PM Comprehensive Report -- ",
                   format(Sys.Date(),"%Y-%m-%d")," (",n_ok," OK, ",n_fail," failed)")) %>%
    text(paste0(
      "FPREN Daily 2PM Comprehensive Report\n=====================================\n\n",
      "Generated: ", format(Sys.time(),"%Y-%m-%d %H:%M ET"), "\n\n",
      "Reports generated (", n_ok, "):\n", gen_lines, "\n\n",
      "Failures (", n_fail, "):\n", fail_lines, "\n\n",
      "Output directory: ", REPORT_DIR, "\n\n",
      "-- FPREN Automated Reporting System\n   Florida Public Radio Emergency Network\n"
    ))
  server(host=smtp_host, port=smtp_port, reuse=FALSE)(em, verbose=FALSE)
  log_msg("Summary email sent to", mail_to)
}, error=function(e) log_msg("ERROR sending email:", conditionMessage(e)))

log_msg("=== Done ===", n_ok, "reports generated,", n_fail, "failed.")
cat("COMPREHENSIVE_REPORT_COMPLETE\n")
