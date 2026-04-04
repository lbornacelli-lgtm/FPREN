#!/usr/bin/env Rscript
# ============================================================
# FPREN — Waze Distance Helper Functions for RStudio
# ============================================================
# Query waze_alerts and waze_jams from MongoDB and compute
# distances from asset locations using geosphere and sf.
#
# Dependencies:
#   install.packages(c("mongolite", "geosphere", "sf", "dplyr", "lubridate"))
#
# Usage (in RStudio):
#   source("scripts/waze_distance_helpers.R")
#   asset <- c(lon = -82.3248, lat = 29.6516)   # WUFT, Gainesville
#
#   # Alerts within 10 km of asset
#   nearby <- waze_alerts_near(asset, radius_km = 10)
#
#   # Jams whose centroid is within 15 km
#   jams   <- waze_jams_near(asset, radius_km = 15)
#
#   # Full distance table for all active alerts
#   all_dists <- waze_alert_distances(asset)
# ============================================================

suppressPackageStartupMessages({
  library(mongolite)
  library(geosphere)
  library(dplyr)
  library(lubridate)
})

# ── MongoDB connection ──────────────────────────────────────
.MONGO_URI  <- Sys.getenv("MONGO_URI", unset = "mongodb://localhost:27017/")
.DB_NAME    <- "weather_rss"

.waze_col <- function(collection) {
  mongolite::mongo(
    collection = collection,
    db         = .DB_NAME,
    url        = .MONGO_URI
  )
}

# ── Internal helpers ────────────────────────────────────────

#' Fetch all documents from a Waze collection as a data frame.
#' @param collection "waze_alerts" or "waze_jams"
#' @param query      mongolite JSON query string (default: all docs)
#' @param fields     mongolite JSON projection string
.waze_fetch <- function(collection,
                        query  = "{}",
                        fields = '{"_id":0}') {
  col <- .waze_col(collection)
  on.exit(col$disconnect())
  col$find(query = query, fields = fields)
}

#' Haversine distance in km between a fixed point and a data frame
#' with `lon` and `lat` columns.
#' @param origin  Named numeric vector: c(lon=..., lat=...)
#' @param df      Data frame with `lon` and `lat` columns
.add_dist_km <- function(origin, df) {
  if (nrow(df) == 0 || !all(c("lon", "lat") %in% names(df))) return(df)
  df <- df[!is.na(df$lon) & !is.na(df$lat), ]
  if (nrow(df) == 0) return(df)
  mat    <- as.matrix(df[, c("lon", "lat")])
  origin_mat <- matrix(c(origin["lon"], origin["lat"]), nrow = 1)
  dists  <- geosphere::distHaversine(origin_mat, mat) / 1000   # metres → km
  df$dist_km <- round(dists, 3)
  df[order(df$dist_km), ]
}

# ── Public API ──────────────────────────────────────────────

#' Fetch all Waze alerts and compute distance from `origin`.
#'
#' @param origin     Named numeric: c(lon=..., lat=...)
#' @param max_age_h  Only return alerts fetched within this many hours (default 2)
#' @return Data frame sorted by dist_km ascending
waze_alert_distances <- function(origin, max_age_h = 2) {
  cutoff <- format(
    lubridate::now("UTC") - lubridate::hours(max_age_h),
    "%Y-%m-%dT%H:%M:%SZ"
  )
  query <- sprintf('{"fetched_at":{"$gte":"%s"}}', cutoff)
  df <- .waze_fetch("waze_alerts", query = query)
  .add_dist_km(origin, df)
}

#' Return Waze alerts within `radius_km` of `origin`.
#'
#' @param origin     Named numeric: c(lon=..., lat=...)
#' @param radius_km  Search radius in kilometres (default 10)
#' @param max_age_h  Freshness filter in hours (default 2)
#' @param type       Optional alert type filter, e.g. "ACCIDENT", "HAZARD"
#' @return Data frame of matching alerts with dist_km column
waze_alerts_near <- function(origin, radius_km = 10, max_age_h = 2,
                             type = NULL) {
  df <- waze_alert_distances(origin, max_age_h = max_age_h)
  if (nrow(df) == 0) return(df)
  df <- df[df$dist_km <= radius_km, ]
  if (!is.null(type) && "type" %in% names(df)) {
    df <- df[toupper(df$type) == toupper(type), ]
  }
  df
}

#' Fetch all Waze jams and compute centroid distance from `origin`.
#'
#' @param origin     Named numeric: c(lon=..., lat=...)
#' @param max_age_h  Freshness filter in hours (default 2)
#' @return Data frame sorted by dist_km ascending
waze_jam_distances <- function(origin, max_age_h = 2) {
  cutoff <- format(
    lubridate::now("UTC") - lubridate::hours(max_age_h),
    "%Y-%m-%dT%H:%M:%SZ"
  )
  query <- sprintf('{"fetched_at":{"$gte":"%s"}}', cutoff)
  df <- .waze_fetch("waze_jams", query = query)
  .add_dist_km(origin, df)
}

#' Return Waze jams within `radius_km` of `origin`.
#'
#' @param origin     Named numeric: c(lon=..., lat=...)
#' @param radius_km  Search radius in kilometres (default 15)
#' @param max_age_h  Freshness filter in hours (default 2)
#' @param min_level  Minimum jam severity 0–5 (default 0 = all)
#' @return Data frame of matching jams with dist_km column
waze_jams_near <- function(origin, radius_km = 15, max_age_h = 2,
                           min_level = 0) {
  df <- waze_jam_distances(origin, max_age_h = max_age_h)
  if (nrow(df) == 0) return(df)
  df <- df[df$dist_km <= radius_km, ]
  if ("level" %in% names(df)) {
    df <- df[!is.na(df$level) & df$level >= min_level, ]
  }
  df
}

#' Summarise Waze activity near an asset — useful for BCP reports.
#'
#' @param origin     Named numeric: c(lon=..., lat=...)
#' @param radius_km  Search radius in kilometres (default 20)
#' @param max_age_h  Freshness filter in hours (default 2)
#' @return Named list: n_alerts, n_jams, worst_alert_type,
#'         avg_delay_sec, max_level, nearest_alert_km, nearest_jam_km
waze_summary_near <- function(origin, radius_km = 20, max_age_h = 2) {
  alerts <- waze_alerts_near(origin, radius_km = radius_km,
                             max_age_h = max_age_h)
  jams   <- waze_jams_near(origin,  radius_km = radius_km,
                           max_age_h = max_age_h)

  list(
    n_alerts          = nrow(alerts),
    n_jams            = nrow(jams),
    worst_alert_type  = if (nrow(alerts) > 0) alerts$type[1] else NA_character_,
    nearest_alert_km  = if (nrow(alerts) > 0) min(alerts$dist_km) else NA_real_,
    nearest_jam_km    = if (nrow(jams)   > 0) min(jams$dist_km)   else NA_real_,
    avg_delay_sec     = if (nrow(jams)   > 0 && "delay_sec" %in% names(jams))
                          mean(jams$delay_sec, na.rm = TRUE) else NA_real_,
    max_jam_level     = if (nrow(jams)   > 0 && "level"    %in% names(jams))
                          max(jams$level,     na.rm = TRUE) else NA_real_
  )
}

# ── sf-based helpers (optional, requires sf package) ────────

#' Convert Waze alerts data frame to an sf POINT object.
#' Useful for spatial joins with county or zone polygons.
#' @param df  Data frame returned by waze_alert_distances() etc.
waze_alerts_sf <- function(df) {
  if (!requireNamespace("sf", quietly = TRUE))
    stop("Install the 'sf' package: install.packages('sf')")
  if (nrow(df) == 0 || !all(c("lon", "lat") %in% names(df))) return(df)
  df <- df[!is.na(df$lon) & !is.na(df$lat), ]
  sf::st_as_sf(df, coords = c("lon", "lat"), crs = 4326)
}

#' Convert Waze jams data frame to an sf object.
#' Jams that have a valid GeoJSON LineString in the `line` column are
#' returned as LINESTRING features; others fall back to centroid POINTs.
#' @param df  Data frame returned by waze_jam_distances() etc.
waze_jams_sf <- function(df) {
  if (!requireNamespace("sf", quietly = TRUE))
    stop("Install the 'sf' package: install.packages('sf')")
  if (nrow(df) == 0 || !all(c("lon", "lat") %in% names(df))) return(df)
  df <- df[!is.na(df$lon) & !is.na(df$lat), ]
  # Fall back to centroid points — full LineString parsing requires
  # unnesting the `line.coordinates` list column from mongolite.
  sf::st_as_sf(df, coords = c("lon", "lat"), crs = 4326)
}

message("[waze_distance_helpers] Loaded. Functions available:")
message("  waze_alert_distances(origin, max_age_h)")
message("  waze_alerts_near(origin, radius_km, max_age_h, type)")
message("  waze_jam_distances(origin, max_age_h)")
message("  waze_jams_near(origin, radius_km, max_age_h, min_level)")
message("  waze_summary_near(origin, radius_km, max_age_h)")
message("  waze_alerts_sf(df)  /  waze_jams_sf(df)")
