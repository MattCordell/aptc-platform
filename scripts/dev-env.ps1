<#
.SYNOPSIS
Brings up a development stack that is ready to walk through by hand: seeded catalogue and a
signed-up administrator who has accepted the terms and has two-factor sign-in configured (NFR-41).

.DESCRIPTION
One command replaces the manual sequence of starting the stack, seeding it, registering a user,
granting a role and setting up an authenticator. In order, it:

  1. Builds and starts the stack: docker compose -f deploy/compose.yml up -d --build
  2. Seeds the sample catalogue (scripts/dev-seed.ps1). A catalogue that already holds data is
     left alone.
  3. Creates the dev user in Keycloak with a password and a TOTP authenticator secret. Keycloak
     runs without a volume, so a restart discards the user; running this script again recreates
     the same user with the same id, so the existing app_user still matches.
  4. Signs that user in once through the API, which creates the app_user row, and accepts the
     current terms of use. The sign-in uses a direct password grant that the script switches on for
     the nptc-frontend client for these few calls and switches off again.
  5. Grants the administrator role (scripts/grant_role.py inside the backend container).

The password and the authenticator secret are generated the first time a username is used and
kept in deploy/.dev-user.json, which git ignores. The file holds one entry per username, and every
later run reuses the entry. They are for this local
stack only: Keycloak here is HTTP-only start-dev with open registration (deploy/compose.yml).

Add the authenticator secret to an authenticator app once, or run this script with -Totp whenever
a code is needed. The script prints the secret and a current code at the end of each run.

Needs: Docker and deploy/.env copied from deploy/.env.example. uv must also be on PATH unless you
pass -SkipSeed.

Exit codes:
  0   ready
  1   a prerequisite is missing: Docker, uv (unless -SkipSeed), or deploy/.env
  20  the stack did not start or did not become reachable
  21  the seed failed
  22  the Keycloak user could not be created or the realm could not be reached
  23  the sign-in, terms acceptance or role grant failed

.PARAMETER Username
The dev user's Keycloak username. Keycloak stores it in lower case.

.PARAMETER SkipBuild
Start the stack without rebuilding the images. Use when only the data or the user needs refreshing.

.PARAMETER SkipSeed
Do not load the sample catalogue.

.PARAMETER Reset
Delete the database and all containers first (docker compose down -v), then start from scratch.

.PARAMETER Totp
Print the dev user's current authenticator code and exit. Changes nothing.

.EXAMPLE
powershell -File scripts/dev-env.ps1

.EXAMPLE
powershell -File scripts/dev-env.ps1 -SkipBuild

.EXAMPLE
powershell -File scripts/dev-env.ps1 -Totp
#>
[CmdletBinding()]
param(
    [string]$Username = 'devadmin',
    [switch]$SkipBuild,
    [switch]$SkipSeed,
    [switch]$Reset,
    [switch]$Totp
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Username = $Username.ToLowerInvariant()

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$ComposeFile = Join-Path $RepoRoot 'deploy\compose.yml'
$EnvFile = Join-Path $RepoRoot 'deploy\.env'
$StateFile = Join-Path $RepoRoot 'deploy\.dev-user.json'
$Realm = 'nptc'
$FrontendClient = 'nptc-frontend'
$Base32Alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
$TotpPeriod = 30
$ExitPrerequisite = 1
$ExitStack = 20
$ExitSeed = 21
$ExitKeycloak = 22
$ExitSignIn = 23

function Stop-WithError {
    param([string]$Message, [int]$Code)
    Write-Host "error: $Message"
    exit $Code
}

function Get-ErrorDetail {
    param($ErrorRecord)
    $detail = $ErrorRecord.Exception.Message
    if ($ErrorRecord.ErrorDetails -and $ErrorRecord.ErrorDetails.Message) {
        $detail += ' ' + $ErrorRecord.ErrorDetails.Message
    }
    return $detail
}

function Read-DotEnv {
    param([string]$Path)
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
            $values[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
        }
    }
    return $values
}

function ConvertTo-Base32 {
    param([byte[]]$Bytes)
    $bits = ''
    foreach ($b in $Bytes) { $bits += [Convert]::ToString($b, 2).PadLeft(8, '0') }
    $out = ''
    for ($i = 0; $i -lt $bits.Length; $i += 5) {
        $chunk = $bits.Substring($i, [Math]::Min(5, $bits.Length - $i)).PadRight(5, '0')
        $out += $Base32Alphabet[[Convert]::ToInt32($chunk, 2)]
    }
    return $out
}

function New-RandomText {
    param([int]$Length, [string]$Alphabet)
    $rng = New-Object System.Security.Cryptography.RNGCryptoServiceProvider
    $bytes = New-Object byte[] $Length
    $rng.GetBytes($bytes)
    $chars = foreach ($b in $bytes) { $Alphabet[$b % $Alphabet.Length] }
    return -join $chars
}

# Keycloak keys the HMAC with the stored secret string's own bytes, and shows the user the Base32
# of those same bytes, so $Secret here is the stored string and the authenticator app gets its
# Base32 form.
function Get-TotpCode {
    param([string]$Secret, [long]$Step)
    $counter = [BitConverter]::GetBytes($Step)
    [Array]::Reverse($counter)
    $hmac = [System.Security.Cryptography.HMACSHA1]::new([Text.Encoding]::ASCII.GetBytes($Secret))
    $hash = $hmac.ComputeHash($counter)
    $offset = $hash[19] -band 0x0f
    # Cast each byte first: PowerShell shifts a [byte] within one byte and drops the shifted bits.
    $binary = ([long]($hash[$offset] -band 0x7f) -shl 24) -bor ([long]$hash[$offset + 1] -shl 16) -bor
        ([long]$hash[$offset + 2] -shl 8) -bor [long]$hash[$offset + 3]
    return ($binary % 1000000).ToString().PadLeft(6, '0')
}

function Get-CurrentTotpStep {
    return [long][Math]::Floor([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() / $TotpPeriod)
}

function Read-DevUserStates {
    if (-not (Test-Path -LiteralPath $StateFile)) { return [pscustomobject]@{} }
    return Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
}

function Get-DevUserState {
    $all = Read-DevUserStates
    $known = $all.PSObject.Properties[$Username]
    if ($known) { return $known.Value }
    $state = [pscustomobject]@{
        username = $Username
        userId = [guid]::NewGuid().ToString()
        password = (New-RandomText 20 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789') + '!9'
        totpSecret = New-RandomText 20 'abcdefghijklmnopqrstuvwxyz0123456789'
    }
    $all | Add-Member -NotePropertyName $Username -NotePropertyValue $state
    $all | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $StateFile -Encoding ASCII
    return $state
}

# Keycloak refuses a code it has already accepted in the same 30 s step, such as one just typed
# into a browser, so a refused first attempt waits for the next step and tries once more.
function Get-DevToken {
    param([string]$KeycloakUrl, $State)
    foreach ($attempt in 1..2) {
        $step = Get-CurrentTotpStep
        try {
            $token = Invoke-RestMethod -Method Post -Uri "$KeycloakUrl/realms/$Realm/protocol/openid-connect/token" -Body @{
                grant_type = 'password'
                client_id = $FrontendClient
                scope = 'openid'
                username = $State.username
                password = $State.password
                totp = (Get-TotpCode $State.totpSecret $step)
            }
            return [pscustomobject]@{ Token = $token; Step = $step }
        }
        catch {
            if ($attempt -eq 2 -or (Get-ErrorDetail $_) -notmatch 'invalid_grant') { throw }
            Write-Host 'Keycloak refused the sign-in. Waiting for the next authenticator code to try once more.'
            while ((Get-CurrentTotpStep) -eq $step) { Start-Sleep -Seconds 1 }
        }
    }
}

function Get-AdminHeaders {
    param([string]$KeycloakUrl, [hashtable]$Settings)
    $token = Invoke-RestMethod -Method Post -Uri "$KeycloakUrl/realms/master/protocol/openid-connect/token" -Body @{
        grant_type = 'password'
        client_id = 'admin-cli'
        username = $Settings['KEYCLOAK_ADMIN_USER']
        password = $Settings['KEYCLOAK_ADMIN_PASSWORD']
    }
    return @{ Authorization = "Bearer $($token.access_token)" }
}

function Set-DirectGrant {
    param([string]$KeycloakUrl, [hashtable]$Headers, [bool]$Enabled)
    $clients = Invoke-RestMethod -Headers $Headers -Uri "$KeycloakUrl/admin/realms/$Realm/clients?clientId=$FrontendClient"
    $client = Invoke-RestMethod -Headers $Headers -Uri "$KeycloakUrl/admin/realms/$Realm/clients/$($clients[0].id)"
    $client.directAccessGrantsEnabled = $Enabled
    Invoke-RestMethod -Method Put -Headers $Headers -ContentType 'application/json' `
        -Uri "$KeycloakUrl/admin/realms/$Realm/clients/$($clients[0].id)" `
        -Body ($client | ConvertTo-Json -Depth 20) | Out-Null
}

if ($Totp) {
    $existing = (Read-DevUserStates).PSObject.Properties[$Username]
    if (-not $existing) {
        Stop-WithError "no dev user '$Username' yet. Run: powershell -File scripts/dev-env.ps1" $ExitPrerequisite
    }
    $step = Get-CurrentTotpStep
    $remaining = $TotpPeriod - ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() % $TotpPeriod)
    Write-Host ("{0}  (valid for about {1} s)" -f (Get-TotpCode $existing.Value.totpSecret $step), $remaining)
    exit 0
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-WithError 'docker is not on PATH.' $ExitPrerequisite
}
if (-not $SkipSeed -and -not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Stop-WithError 'uv is not on PATH. Install it or add it to PATH, or pass -SkipSeed.' $ExitPrerequisite
}
if (-not (Test-Path -LiteralPath $EnvFile)) {
    Stop-WithError 'deploy/.env is missing. Copy deploy/.env.example to deploy/.env and set the passwords.' $ExitPrerequisite
}

$settings = Read-DotEnv $EnvFile
$keycloakPort = if ($settings.ContainsKey('KEYCLOAK_PORT') -and $settings['KEYCLOAK_PORT']) { $settings['KEYCLOAK_PORT'] } else { '8080' }
$webPort = if ($settings.ContainsKey('NPTC_WEB_PORT') -and $settings['NPTC_WEB_PORT']) { $settings['NPTC_WEB_PORT'] } else { '8081' }
$keycloakUrl = "http://localhost:$keycloakPort"
$webUrl = "http://localhost:$webPort"
$state = Get-DevUserState

Push-Location $RepoRoot
try {
    if ($Reset) {
        Write-Host '==> Delete the containers and the database'
        & docker compose -f $ComposeFile down -v | Out-Host
        if ($LASTEXITCODE -ne 0) { Stop-WithError "docker compose down failed (exit $LASTEXITCODE)" $ExitStack }
    }

    Write-Host '==> Start the stack'
    $upArguments = @('compose', '-f', $ComposeFile, 'up', '-d')
    if (-not $SkipBuild) { $upArguments += '--build' }
    & docker @upArguments | Out-Host
    if ($LASTEXITCODE -ne 0) { Stop-WithError "docker compose up failed (exit $LASTEXITCODE)" $ExitStack }

    Write-Host '==> Wait for the web app and the realm'
    $ready = $false
    foreach ($attempt in 1..90) {
        try {
            Invoke-RestMethod -Uri "$webUrl/api/v1/openapi.json" -TimeoutSec 3 | Out-Null
            Invoke-RestMethod -Uri "$keycloakUrl/realms/$Realm/.well-known/openid-configuration" -TimeoutSec 3 | Out-Null
            $ready = $true
            break
        }
        catch { Start-Sleep -Seconds 2 }
    }
    if (-not $ready) { Stop-WithError "the stack did not answer at $webUrl and $keycloakUrl within 3 minutes" $ExitStack }

    if (-not $SkipSeed) {
        Write-Host '==> Seed the sample catalogue'
        & powershell -NoProfile -File (Join-Path $PSScriptRoot 'dev-seed.ps1') | Out-Host
        if ($LASTEXITCODE -eq 4) { Write-Host 'The catalogue already holds data, so the seed was skipped.' }
        elseif ($LASTEXITCODE -ne 0) { Stop-WithError "the seed failed (exit $LASTEXITCODE)" $ExitSeed }
    }

    Write-Host "==> Create the dev user '$($state.username)' in Keycloak"
    try {
        $headers = Get-AdminHeaders $keycloakUrl $settings
        # Windows PowerShell 5.1 turns an empty JSON array into one null item, so drop empty items.
        $found = @(Invoke-RestMethod -Headers $headers -Uri "$keycloakUrl/admin/realms/$Realm/users?username=$($state.username)&exact=true" |
            Where-Object { $_ })
        if ($found.Count -gt 0) {
            Write-Host 'The user already exists in Keycloak.'
        }
        else {
            $otpData = @{ subType = 'totp'; digits = 6; period = $TotpPeriod; algorithm = 'HmacSHA1' } | ConvertTo-Json -Compress
            $otpSecret = @{ value = $state.totpSecret } | ConvertTo-Json -Compress
            $user = @{
                id = $state.userId
                username = $state.username
                email = "$($state.username)@example.invalid"
                emailVerified = $true
                firstName = 'Dev'
                lastName = 'Administrator'
                enabled = $true
                credentials = @(
                    @{ type = 'password'; value = $state.password; temporary = $false },
                    @{ type = 'otp'; secretData = $otpSecret; credentialData = $otpData }
                )
            }
            Invoke-RestMethod -Method Post -Headers $headers -ContentType 'application/json' `
                -Uri "$keycloakUrl/admin/realms/$Realm/users" -Body ($user | ConvertTo-Json -Depth 10) | Out-Null
        }
    }
    catch { Stop-WithError "Keycloak refused the request: $(Get-ErrorDetail $_)" $ExitKeycloak }

    Write-Host '==> Sign in once, accept the terms and grant the administrator role'
    $directGrantOn = $false
    $usedStep = $null
    try {
        Set-DirectGrant $keycloakUrl $headers $true
        $directGrantOn = $true
        $signIn = Get-DevToken $keycloakUrl $state
        $usedStep = $signIn.Step
        $api = @{ Authorization = "Bearer $($signIn.Token.access_token)" }
        Invoke-RestMethod -Headers $api -Uri "$webUrl/api/v1/auth/me" | Out-Null
        $terms = Invoke-RestMethod -Headers $api -Uri "$webUrl/api/v1/auth/terms"
        if (-not $terms.accepted) {
            Invoke-RestMethod -Method Post -Headers $api -ContentType 'application/json' `
                -Uri "$webUrl/api/v1/auth/terms/acceptance" -Body (@{ version = $terms.version } | ConvertTo-Json) | Out-Null
        }
    }
    catch {
        Write-Host "If Keycloak rejected the sign-in twice, '$($state.username)' probably exists with other credentials, made by hand or by a run that lost its saved entry."
        Write-Host "Pass another -Username, or delete '$($state.username)' in the Keycloak admin console and run this script again."
        Stop-WithError "the sign-in or terms acceptance failed: $(Get-ErrorDetail $_)" $ExitSignIn
    }
    finally {
        if ($directGrantOn) {
            # The admin token fetched earlier lasts about a minute, so fetch a fresh one.
            try { Set-DirectGrant $keycloakUrl (Get-AdminHeaders $keycloakUrl $settings) $false }
            catch {
                Write-Host "WARNING: could not switch the direct password grant off for ${FrontendClient}: $(Get-ErrorDetail $_)"
                Write-Host 'It stays on until Keycloak restarts. Recreate it now: docker compose -f deploy/compose.yml up -d --force-recreate keycloak'
            }
        }
    }

    & docker compose -f $ComposeFile exec -T backend python scripts/grant_role.py --username $state.username --role administrator | Out-Host
    if ($LASTEXITCODE -ne 0) { Stop-WithError "the role grant failed (exit $LASTEXITCODE)" $ExitSignIn }

    # Keycloak refuses a code it has already accepted, so wait for the next one before printing it.
    while ((Get-CurrentTotpStep) -eq $usedStep) { Start-Sleep -Seconds 1 }
    $secretForApp = ConvertTo-Base32 ([Text.Encoding]::ASCII.GetBytes($state.totpSecret))

    Write-Host ''
    Write-Host 'Ready.'
    Write-Host "  URL:           $webUrl"
    Write-Host "  Username:      $($state.username)"
    Write-Host "  Password:      $($state.password)"
    Write-Host "  Authenticator: $secretForApp  (6 digits, 30 s, SHA-1)"
    Write-Host "  Current code:  $(Get-TotpCode $state.totpSecret (Get-CurrentTotpStep))"
    Write-Host 'Get a fresh code any time: powershell -File scripts/dev-env.ps1 -Totp'
    exit 0
}
finally {
    Pop-Location
}
