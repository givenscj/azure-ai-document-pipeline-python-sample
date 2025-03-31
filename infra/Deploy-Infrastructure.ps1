param
(
    [Parameter(Mandatory = $true)]
    [string]$DeploymentName,
    [Parameter(Mandatory = $true)]
    [string]$Location,
    [switch]$WhatIf
)

Write-Host "Starting infrastructure deployment..."

Push-Location -Path $PSScriptRoot

$principalId = (az ad signed-in-user show --query id -o tsv)
$identity = @{ 
    "principalId"   = "$principalId"
    "principalType" = "User" 
} 
$identityArray = ConvertTo-Json @($identity) -Depth 5 -Compress
     
if ($whatIf) {
    Write-Host "Previewing infrastructure deployment. No changes will be made."
    
    $result = (az deployment sub what-if `
            --name $deploymentName `
            --location $location `
            --template-file './main.bicep' `
            --parameters './main.bicepparam' `
            --parameters workloadName=$deploymentName `
            --parameters location=$location `
            --parameters identities=$identityArray `
            --no-pretty-print) | ConvertFrom-Json
    
    if (-not $result) {
        Write-Error "Infrastructure deployment preview failed."
        exit 1
    }
    
    Write-Host "Infrastructure deployment preview succeeded."
    $result.changes | Format-List
    exit
}
    
$deploymentOutputs = (az deployment sub create `
        --name $deploymentName `
        --location $location `
        --template-file './main.bicep' `
        --parameters './main.bicepparam' `
        --parameters workloadName=$deploymentName `
        --parameters location=$location `
        --parameters identities=$identityArray `
        --query properties.outputs -o json) | ConvertFrom-Json
    
if (-not $deploymentOutputs) {
    Write-Error "Infrastructure deployment failed."
    exit 1
}
    
Write-Host "Infrastructure deployment succeeded."
$deploymentOutputs | Format-List
$deploymentOutputs | ConvertTo-Json | Out-File -FilePath './InfrastructureOutputs.json' -Encoding utf8

Pop-Location
    
return $deploymentOutputs