---
name: api_fetching
what_it_does: Teaches how to fetch structured HTTP API data through the general run_command tool without dumping large pages into context.
when_to_use: A task needs live or external data from an HTTP endpoint, JSON API, REST API, public text endpoint, or compact machine-readable source and no dedicated tool exists.
description: What it does: Teaches how to fetch structured HTTP API data through the general run_command tool without dumping large pages into context. When to use: A task needs live or external data from an HTTP endpoint, JSON API, REST API, public text endpoint, or compact machine-readable source and no dedicated tool exists.
---

# Skill: api_fetching

## Purpose
Use this skill when a task needs live or external data and a normal HTTP request can return compact structured evidence. This is not a special API tool. It teaches how to use the existing `run_command` tool well.

## Core Rule
Prefer structured API/text responses over screenshots, browser captures, or full HTML dumps.

Use `Invoke-RestMethod` when the endpoint returns JSON or XML. Use `Invoke-WebRequest` only when you need raw text or HTML and there is no structured endpoint.

## PowerShell Pattern
Call `run_command` with a compact PowerShell command:

```powershell
$uri = 'https://example.com/api/resource'
$data = Invoke-RestMethod -Uri $uri -TimeoutSec 20
[pscustomobject]@{
  value = $data.value
  source = $uri
} | ConvertTo-Json -Compress
```

## Output Discipline
- Return only the fields needed to answer the user.
- Use `[pscustomobject]` to shape output.
- End with `ConvertTo-Json -Compress` for structured output.
- Do not return full API payloads unless the user asks for raw data.
- Do not fetch full HTML pages when a JSON endpoint is available.
- Do not pipe a large page or object through `ConvertTo-Json -Depth 10` unless you have already selected a small subset.

## URL Parameters
Build URLs safely:

```powershell
$query = 'location or search text'
$encoded = [uri]::EscapeDataString($query)
$uri = "https://example.com/search?q=$encoded"
```

## Error Handling
If the endpoint can fail, wrap the request:

```powershell
try {
  $data = Invoke-RestMethod -Uri $uri -TimeoutSec 20
  [pscustomobject]@{ ok = $true; result = $data } | ConvertTo-Json -Compress
} catch {
  [pscustomobject]@{ ok = $false; error = $_.Exception.Message; source = $uri } | ConvertTo-Json -Compress
}
```

If an API fails, try one alternate reliable text/API source before giving up.

## Final Answer
Answer from the compact structured result. Mention the source briefly when it matters.
