---
name: weather
what_it_does: Teaches how to answer current weather questions for any location using current context and compact API/text fetching through general tools.
when_to_use: The user asks for current weather, weather outside, weather here, weather now, forecast, humidity, wind, temperature, or conditions for an explicit or configured location.
description: What it does: Teaches how to answer current weather questions for any location using current context and compact API/text fetching through general tools. When to use: The user asks for current weather, weather outside, weather here, weather now, forecast, humidity, wind, temperature, or conditions for an explicit or configured location.
---

# Skill: weather

## Purpose
Use this skill to answer weather questions for any location. This is not a dedicated weather tool. It teaches how to combine current context with general command/API tools.

## Location Rule
- If the user explicitly names a location, use that location.
- If the user asks "outside", "here", "near me", or "weather now" without naming a location, call `get_current_datetime_location` and use its configured `location`.
- If no explicit location exists and the configured location is empty or generic, ask for the location instead of guessing.
- Do not store or hardcode personal locations in prompts, skills, code, or examples.

## Time Rule
Always call `get_current_datetime_location` for weather tasks. Use it for local date/time, timezone, and relative wording. If the user supplied an explicit location, do not replace it with the configured location; use the tool result only for time context.

## Current Weather Workflow
1. Load this skill when the task is weather-related.
2. Call `get_current_datetime_location`.
3. Resolve the target location from the user message or configured context.
4. Use `run_command` with PowerShell to fetch compact weather data from a text/API source.
5. Return temperature, feels-like temperature, condition, humidity, wind, and observation time when available.

PowerShell pattern:

```powershell
$location = 'LOCATION_FROM_USER_OR_CONTEXT'
$encoded = [uri]::EscapeDataString($location)
$uri = 'https://wttr.in/' + $encoded + '?format=j1'
$w = Invoke-RestMethod -Uri $uri -TimeoutSec 20

if (-not $w.current_condition -or -not $w.nearest_area) {
  [pscustomobject]@{
    ok = $false
    error = 'Weather source returned no current_condition or nearest_area fields.'
    requested_location = $location
    source = $uri
  } | ConvertTo-Json -Compress
  exit 1
}

$c = $w.current_condition[0]
$a = $w.nearest_area[0]
[pscustomobject]@{
  ok = $true
  requested_location = $location
  resolved_location = $a.areaName[0].value
  region = $a.region[0].value
  country = $a.country[0].value
  temp_C = $c.temp_C
  feels_like_C = $c.FeelsLikeC
  condition = $c.weatherDesc[0].value
  humidity_percent = $c.humidity
  wind = "$($c.winddir16Point) $($c.windspeedKmph) km/h"
  observation_time = $c.localObsDateTime
  source = $uri
} | ConvertTo-Json -Compress
```

## Forecast Workflow
If the user asks for a forecast, extract only the needed days/hours:

```powershell
$location = 'LOCATION_FROM_USER_OR_CONTEXT'
$encoded = [uri]::EscapeDataString($location)
$uri = 'https://wttr.in/' + $encoded + '?format=j1'
$w = Invoke-RestMethod -Uri $uri -TimeoutSec 20

if (-not $w.weather) {
  [pscustomobject]@{
    ok = $false
    error = 'Weather source returned no forecast records.'
    requested_location = $location
    source = $uri
  } | ConvertTo-Json -Compress
  exit 1
}

$w.weather | Select-Object -First 3 | ForEach-Object {
  [pscustomobject]@{
    ok = $true
    date = $_.date
    max_C = $_.maxtempC
    min_C = $_.mintempC
    condition = $_.hourly[4].weatherDesc[0].value
    chance_of_rain_percent = $_.hourly[4].chanceofrain
  }
} | ConvertTo-Json -Compress
```

## Source Fallback
If the first API/text fetch fails:
- Try one alternate reliable weather source with `run_command`.
- Use `web_search` only to discover an official or reliable endpoint.
- Do not use `capture_webpage` unless the user explicitly asks for a visual screenshot.

## Final Answer
Give the answer directly. Include the resolved location and source name briefly. Do not tell the user to check a weather site themselves.
