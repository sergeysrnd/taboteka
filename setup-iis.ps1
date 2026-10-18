param([int]$Port=8765,[string]$PythonPath='',[string]$TaskUser='',[switch]$RestartApi)
$ErrorActionPreference='Stop'
$appRoot=$PSScriptRoot
$logPath=Join-Path $appRoot 'data\iis-setup.log'
New-Item -ItemType Directory -Path (Join-Path $appRoot 'data') -Force | Out-Null
Start-Transcript -Path $logPath -Append
try {
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    $principal=[Security.Principal.WindowsPrincipal]::new($identity)
    if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Run this script as Administrator.'}
    Add-Type -Path "$env:windir\System32\inetsrv\Microsoft.Web.Administration.dll"
    $manager=[Microsoft.Web.Administration.ServerManager]::new()
    $snapshot=[pscustomobject]@{
        captured_at=(Get-Date).ToString('o')
        version=(Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\InetStp').VersionString
        sites=@($manager.Sites | ForEach-Object {[pscustomobject]@{name=$_.Name;state=$_.State.ToString();bindings=@($_.Bindings | ForEach-Object {$_.Protocol+' '+$_.BindingInformation})}})
        modules=@($manager.GetApplicationHostConfiguration().GetSection('system.webServer/globalModules').GetCollection() | ForEach-Object {$_.GetAttributeValue('name')})
        features=@('IIS-StaticContent','IIS-DefaultDocument' | ForEach-Object {Get-WindowsOptionalFeature -Online -FeatureName $_ -ErrorAction SilentlyContinue | Select-Object FeatureName,State})
    }
    if(-not (Test-Path -LiteralPath (Join-Path $appRoot 'data\iis-before.json'))){$snapshot | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $appRoot 'data\iis-before.json') -Encoding utf8}
    $snapshot | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $appRoot 'data\iis-inspection.json') -Encoding utf8
    $siteName='Taboteka'
    $poolName='Taboteka'
    $taskName='Taboteka API'
    $dist=(Resolve-Path -LiteralPath (Join-Path $appRoot 'dist')).Path
    if(-not (Test-Path -LiteralPath (Join-Path $dist 'index.html'))){throw 'Build the frontend first: pnpm run build'}
    $config=Get-Content -LiteralPath (Join-Path $appRoot 'config.json') -Raw | ConvertFrom-Json
    if($config.site_port -ne $Port -or $config.site_url -ne "http://127.0.0.1:$Port"){throw 'Port must match site_port/site_url in config.json; run configure.py first.'}
    if(-not $PythonPath){
        $pythonFile=Join-Path $appRoot 'data\python-path.txt'
        if(Test-Path -LiteralPath $pythonFile){$PythonPath=(Get-Content -LiteralPath $pythonFile -Raw).Trim()}else{$PythonPath=(Get-Command python -ErrorAction Stop).Source}
    }
    $pythonw=Join-Path (Split-Path -Parent $PythonPath) 'pythonw.exe'
    if(-not (Test-Path -LiteralPath $pythonw)){throw "Python runtime not found: $pythonw"}
    $serverScript=Join-Path $appRoot 'server.py'
    $existingTask=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if($existingTask){
        $actions=@($existingTask.Actions)
        if($actions.Count -ne 1 -or $actions[0].Arguments -ne ('"'+$serverScript+'"')){throw 'A different Taboteka API task already exists. It was not changed.'}
    }
    foreach($other in $manager.Sites){
        if($other.Name -ne $siteName){foreach($application in $other.Applications){
            if($application.ApplicationPoolName -eq $poolName){throw "Pool $poolName is shared with site $($other.Name). It was not changed."}
        }}
    }
    foreach($existing in $manager.Sites){
        if($existing.Name -ne $siteName){foreach($binding in $existing.Bindings){
            if($binding.Protocol -eq 'http' -and $binding.EndPoint.Port -eq $Port){throw "Port $Port is used by site $($existing.Name). Existing sites were not changed."}
        }}
    }
    $site=$manager.Sites[$siteName]
    if($site){
        if($site.Applications['/'].VirtualDirectories['/'].PhysicalPath -ne $dist){throw 'A different Taboteka site already exists. It was not changed.'}
        if($site.Bindings.Count -ne 1 -or $site.Bindings[0].Protocol -ne 'http' -or $site.Bindings[0].BindingInformation -ne "127.0.0.1:${Port}:"){throw 'Existing Taboteka bindings differ from the requested loopback binding. No changes were saved.'}
    }else{
        $site=$manager.Sites.Add($siteName,'http',"127.0.0.1:${Port}:",$dist)
    }
    $pool=$manager.ApplicationPools[$poolName]
    if(-not $pool){$pool=$manager.ApplicationPools.Add($poolName)}
    $pool.ManagedRuntimeVersion=''
    $pool.ProcessModel.IdentityType=[Microsoft.Web.Administration.ProcessModelIdentityType]::ApplicationPoolIdentity
    $site.Applications['/'].ApplicationPoolName=$poolName
    $site.ServerAutoStart=$true
    $anonymous=$manager.GetApplicationHostConfiguration().GetSection('system.webServer/security/authentication/anonymousAuthentication',$siteName)
    $anonymous.SetAttributeValue('enabled',$true)
    $anonymous.SetAttributeValue('userName','')
    $manager.CommitChanges()
    # Read access only to the compiled frontend. No archive permissions are changed.
    & icacls.exe $dist /grant "IIS AppPool\${poolName}:(OI)(CI)(RX)" /T /Q
    if($LASTEXITCODE -ne 0){throw 'Could not grant read access to frontend files.'}
    Start-Service WAS
    Start-Service W3SVC
    $manager.Dispose()
    $manager=[Microsoft.Web.Administration.ServerManager]::new()
    $site=$manager.Sites[$siteName]
    if($site.State -ne [Microsoft.Web.Administration.ObjectState]::Started){$site.Start() | Out-Null}
    $pool=$manager.ApplicationPools[$poolName]
    if($pool.State -ne [Microsoft.Web.Administration.ObjectState]::Started){$pool.Start() | Out-Null}
    if(-not $TaskUser){$TaskUser=$identity.Name}
    $action=New-ScheduledTaskAction -Execute $pythonw -Argument ('"'+(Join-Path $appRoot 'server.py')+'"') -WorkingDirectory $appRoot
    $trigger=New-ScheduledTaskTrigger -AtLogOn -User $TaskUser
    $taskPrincipal=New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType Interactive -RunLevel Limited
    $taskSettings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    $task=New-ScheduledTask -Action $action -Trigger $trigger -Principal $taskPrincipal -Settings $taskSettings -Description 'Taboteka local API for IIS and Guitar Pro desktop integration'
    Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
    # Adopt the registered task without competing with an existing manual API.
    $apiPort=(Get-Content -LiteralPath (Join-Path $appRoot 'config.json') -Raw | ConvertFrom-Json).port
    $apiAlive=$false
    try{$response=Invoke-WebRequest -Uri "http://127.0.0.1:$apiPort/api/stats" -UseBasicParsing -TimeoutSec 3;$apiAlive=$response.StatusCode -eq 200}catch{}
    if($RestartApi){
        Stop-ScheduledTask -TaskName $taskName
        Start-Sleep -Seconds 1
        $apiAlive=$false
        try{$response=Invoke-WebRequest -Uri "http://127.0.0.1:$apiPort/api/stats" -UseBasicParsing -TimeoutSec 1;$apiAlive=$response.StatusCode -eq 200}catch{}
        if($apiAlive){throw 'An API outside the scheduled task is still running. Stop that manual instance before restarting.'}
    }
    if(-not $apiAlive){Start-ScheduledTask -TaskName $taskName}
    $apiReady=$false
    for($attempt=0;$attempt -lt 30;$attempt++){
        try{$response=Invoke-WebRequest -Uri "http://127.0.0.1:$apiPort/api/stats" -UseBasicParsing -TimeoutSec 2;if($response.StatusCode -eq 200){$apiReady=$true;break}}catch{}
        Start-Sleep -Milliseconds 500
    }
    if(-not $apiReady){throw 'IIS is configured, but the API did not start. Check the task user, config.json and data/server.log.'}
    $result=[pscustomobject]@{ok=$true;site=$siteName;pool=$poolName;url="http://127.0.0.1:$Port";path=$dist;state=$site.State.ToString();api_task=$taskName;api_user=$TaskUser;created_at=(Get-Date).ToString('o')}
    $result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $appRoot 'data\iis-result.json') -Encoding utf8
    $result | Format-List
} catch {
    [pscustomobject]@{ok=$false;error=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $appRoot 'data\iis-result.json') -Encoding utf8
    Write-Error $_
    exit 1
} finally {Stop-Transcript}
