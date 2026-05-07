# Job Types

## Single Point (SP)

Energy calculation on a fixed geometry:

```
$rem
   JOBTYPE          sp
   METHOD           b3lyp
   BASIS            6-31g*
$end
```

Use when: You only need the energy, no geometry changes.

## Geometry Optimization (Opt)

Finds the minimum energy structure:

```
$rem
   JOBTYPE          opt
   METHOD           b3lyp
   BASIS            6-31g*
$end
```

Q-Chem optimizes by iteratively adjusting geometry until forces are small.

### Opt Options

| Keyword | Value | Meaning |
|---------|-------|---------|
| `GEOMETRY_OPTIMIZE` | true | Perform optimization |
| `MAX_OPT_CYCLES` | 50 | Maximum optimization steps |
| `OPT_TOL_GRAD` | 300 | Gradient threshold (10⁻⁶) |
| `OPT_TOL_DISPLACEMENT` | 300 | Displacement threshold |
| `OPT_TOL_ENERGY` | 300 | Energy threshold (10⁻⁶ Hartree) |

### Transition State (TS) Optimization

```
$rem
   JOBTYPE          ts
   METHOD           b3lyp
   BASIS            6-31g*
$end
```

TS search requires a good starting guess (approximate saddle point).

## Frequency Calculation (Freq)

Vibrational analysis and thermochemical corrections:

```
$rem
   JOBTYPE          freq
   METHOD           b3lyp
   BASIS            6-31g*
$end
```

Outputs:
- Zero-point energy (ZPE)
- Enthalpy corrections
- Entropy
- Heat capacity
- Thermodynamic properties
- Vibrational frequencies

**Always run Freq at the same level as Opt** for accurate thermochemistry.

**Important:** For a stable molecule, all frequencies should be real (>0).
Imaginary frequencies (negative in output) indicate a transition state.

## Combined Opt + Freq

Run sequentially using batch jobs:

```
$rem
   JOBTYPE          opt
   METHOD           b3lyp
   BASIS            6-31g*
$end

@@@

$molecule
read
$end

$rem
   JOBTYPE          freq
   METHOD           b3lyp
   BASIS            6-31g*
$end
```

## Excited States (TDDFT)

Time-dependent DFT for excited states:

```
$rem
   JOBTYPE          sp
   METHOD           b3lyp
   BASIS            6-311+g*
   CIS_N_ROOTS      10
   CIS_SINGLE_ROOT  0
$end
```

Requests 10 excited states.

### TDDFT Options

| Keyword | Value | Purpose |
|---------|-------|---------|
| `CIS_N_ROOTS` | integer | Number of excited states |
| `CIS_SINGLE_ROOT` | integer | Target specific state |
| `RPA` | true/false | Use RPA instead of TDA |
| `CIS_STATE_DERIV` | integer | State for gradients |

### Excited State Optimization

```
$rem
   JOBTYPE          opt
   METHOD           b3lyp
   CIS_N_ROOTS      5
   CIS_STATE_DERIV  1
$end
```

## CIS (Configuration Interaction Singles)

For HF-based excited states:

```
$rem
   JOBTYPE          sp
   METHOD           hf
   CIS_N_ROOTS      10
$end
```

## Properties

### Dipole Moment and Multipoles

Automatic with all calculations.

### NMR Chemical Shifts

```
$rem
   JOBTYPE          nmr
   METHOD           b3lyp
   BASIS            6-311+g**
   NMR_SPIN_SPIN    true
$end
```

### Polarizability

```
$rem
   JOBTYPE          polarizability
   METHOD           b3lyp
   BASIS            6-311+g*
$end
```

## Potential Energy Scan

Relaxed scan along coordinates:

```
$rem
   JOBTYPE          pes_scan
   METHOD           b3lyp
   BASIS            6-31g*
$end

$scan
   bond 1 2 1.5 2.5 10
$end
```

## IRC (Intrinsic Reaction Coordinate)

Follow the reaction path from TS:

```
$rem
   JOBTYPE          rpath
   METHOD           b3lyp
   BASIS            6-31g*
$end
```

Requires: A valid transition state geometry.

## Job Sequencing

For complex workflows, use batch jobs with `@@@`:

```
$molecule
0 1
O  0.0  0.0  0.0
H  0.0  0.9  0.0
$end

$rem
   JOBTYPE          opt
   METHOD           b3lyp
   BASIS            6-31g*
$end

@@@

$molecule
read
$end

$rem
   JOBTYPE          freq
   METHOD           b3lyp
   BASIS            6-31g*
$end

@@@

$molecule
read
$end

$rem
   JOBTYPE          sp
   METHOD           mp2
   BASIS            cc-pVTZ
$end
```

This performs:
1. B3LYP/6-31G* optimization
2. B3LYP/6-31G* frequency
3. MP2/cc-pVTZ single point

## Common Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| Job hangs | Missing $end | Check section endings |
| Wrong energy | Wrong JOBTYPE | Verify job type matches intent |
| No convergence | SCF issues | See SCF convergence guide |
| Imaginary freq | Not minimum | Continue optimization |