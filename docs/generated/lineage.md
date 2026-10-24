# Lineage

```mermaid
graph LR
  bronze_activities[bronze_activities<br/>bronze]
  silver_activities[silver_activities<br/>silver]
  gold_weekly_summary[gold_weekly_summary<br/>gold]
  bronze_activities --> silver_activities
  silver_activities --> gold_weekly_summary
  bronze_activities -. activity_id --> activity_id .- silver_activities
  bronze_activities -. event_id --> event_id .- silver_activities
  bronze_activities -. event_name --> event_name .- silver_activities
  bronze_activities -. athlete_name --> athlete_name .- silver_activities
  bronze_activities -. started_at --> started_at .- silver_activities
  bronze_activities -. distance_km --> distance_km .- silver_activities
  bronze_activities -. duration_s --> duration_s .- silver_activities
  bronze_activities -. pace_sec_per_km --> pace_sec_per_km .- silver_activities
  bronze_activities -. elevation_gain_m --> elevation_gain_m .- silver_activities
  bronze_activities -. heart_rate_avg --> heart_rate_avg .- silver_activities
  bronze_activities -. heart_rate_max --> heart_rate_max .- silver_activities
  bronze_activities -. gps_lat --> gps_lat .- silver_activities
  bronze_activities -. gps_lon --> gps_lon .- silver_activities
  bronze_activities -. _source_file --> _source_file .- silver_activities
  bronze_activities -. _ingest_seq --> _ingest_seq .- silver_activities
  bronze_activities -. _ingested_at --> _ingested_at .- silver_activities
  silver_activities -. event_id --> event_id .- gold_weekly_summary
  silver_activities -. event_name --> event_name .- gold_weekly_summary
  silver_activities -. started_at --> week .- gold_weekly_summary
  silver_activities -. activity_id --> activities .- gold_weekly_summary
  silver_activities -. distance_km --> total_km .- gold_weekly_summary
  silver_activities -. elevation_gain_m --> total_elevation_gain_m .- gold_weekly_summary
  silver_activities -. heart_rate_avg --> avg_heart_rate .- gold_weekly_summary
  silver_activities -. pace_sec_per_km --> avg_pace_sec_per_km .- gold_weekly_summary
```

- silver_activities.gps_lat → bronze_activities.gps_lat
- silver_activities.gps_lon → bronze_activities.gps_lon
- silver_activities.heart_rate_avg → bronze_activities.heart_rate_avg
- silver_activities.heart_rate_max → bronze_activities.heart_rate_max
- silver_activities.athlete_name → bronze_activities.athlete_name
