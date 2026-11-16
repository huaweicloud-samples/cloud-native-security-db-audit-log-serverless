# ============================================================================
# Layer 4: 定时触发器（FunctionGraph TIMER）
# ============================================================================

# Dispatcher 定时触发（每 5 分钟）
resource "huaweicloud_fgs_function_trigger" "dispatcher_timer" {
  function_urn = huaweicloud_fgs_function.dispatcher.urn
  type         = "TIMER"
  event_data   = jsonencode({
    name         = "dispatcher-schedule"
    schedule_type = "Cron"
    schedule      = var.dispatcher_schedule
  })
}

# Discovery 定时触发（每日 02:00）
resource "huaweicloud_fgs_function_trigger" "discovery_timer" {
  function_urn = huaweicloud_fgs_function.discovery.urn
  type         = "TIMER"
  event_data   = jsonencode({
    name         = "discovery-schedule"
    schedule_type = "Cron"
    schedule      = var.discovery_schedule
  })
}
