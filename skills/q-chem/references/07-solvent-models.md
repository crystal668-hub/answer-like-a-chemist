# Solvent Models

## Overview

Q-Chem supports several implicit solvent models:
- PCM (Polarizable Continuum Model)
- SM8, SM12, SMD
- COSMO

## PCM (Polarizable Continuum Model)

Most commonly used solvent model:

```
$rem
   SOLVENT_MODEL        pcm
$end

$solvent
   SOLVENT_NAME         water
$end
```

### PCM Options

| Keyword | Value | Purpose |
|---------|-------|---------|
| `SOLVENT_NAME` | solvent name | Solvent type |
| `RADIUS_SET` | bondi, uff, etc. | Atomic radii set |

### Available Solvents

| Name | Solvent |
|------|---------|
| water | Water |
| methanol | Methanol |
| ethanol | Ethanol |
| acetonitrile | Acetonitrile |
| acetone | Acetone |
| benzene | Benzene |
| chloroform | Chloroform |
| dichloroethane | 1,2-Dichloroethane |
| dichloromethane | Dichloromethane |
| dmf | DMF |
| dmsco | DMSO |
| ether | Diethyl ether |
| hexane | Hexane |
| toluene | Toluene |
| thf | THF |
| octanol | 1-Octanol |

## SM8 and SMD

Universal solvation models:

```
$rem
   SOLVENT_MODEL        sm8
   SM8_SOLVENT          water
$end
```

Or:

```
$rem
   SOLVENT_MODEL        smd
   SM8_SOLVENT          water
$end
```

SMD is recommended for most applications.

### SM8 Solvent Codes

| Code | Solvent |
|------|---------|
| 1 | water |
| 2 | methanol |
| 3 | ethanol |
| 4 | acetonitrile |
| 5 | acetone |
| 6 | benzene |
| 7 | chloroform |
| 8 | dichloroethane |
| 9 | dichloromethane |
| 10 | dmf |
| 11 | dmsco |
| 12 | ether |
| 13 | hexane |
| 14 | toluene |
| 15 | thf |
| 16 | octanol |

## Full Example

```
$comment
Solvent calculation with PCM
$end

$molecule
0 1
O    0.000000   0.000000   0.000000
H    0.757160   0.586260   0.000000
H   -0.757160   0.586260   0.000000
$end

$rem
   JOBTYPE              sp
   METHOD               b3lyp
   BASIS                6-31g*
   SOLVENT_MODEL        pcm
$end

$solvent
   SOLVENT_NAME         water
$end
```

## Optimization in Solvent

```
$rem
   JOBTYPE              opt
   METHOD               b3lyp
   BASIS                6-31g*
   SOLVENT_MODEL        smd
   SM8_SOLVENT          water
$end
```

## Free Energy Corrections

Solvent models provide:
- Solvation free energy
- Cavitation energy
- Dispersion energy
- Repulsion energy

Check output for:
```
Total free energy in solution
Solvation free energy
```

## Specific Solvation Parameters

Custom solvent parameters:

```
$solvent
   DIELECTRIC           78.5
   SOLVENT_NAME         custom
$end
```

## Non-Equilibrium Solvation

For excited states:

```
$rem
   SOLVENT_MODEL        pcm
   PCM_NON_EQUILIBRIUM  true
$end
```

## Common Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| No solvent section | Missing $solvent | Add $solvent section |
| Unknown solvent | Wrong name | Use correct solvent name |
| Convergence issues | PCM instability | Use SMD instead |
| Wrong energy | Geometry gas-phase | Optimize in solvent |

## Recommendations

| Purpose | Model | Notes |
|---------|-------|-------|
| General | SMD | Good accuracy |
| Specific solvents | PCM | More control |
| Excited states | PCM non-eq | Use non-equilibrium |
| Thermochemistry | SMD | Consistent with thermo |