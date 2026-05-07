# SCF Convergence

## SCF Basics

The Self-Consistent Field (SCF) procedure iteratively solves the electronic structure equations until convergence.

### Convergence Criteria

Default thresholds:
- Energy change < 10⁻⁶ Hartree
- Density matrix change < 10⁻⁵

```
$rem
   SCF_CONVERGENCE      6
$end
```

| Value | Energy threshold | Density threshold |
|-------|------------------|-------------------|
| 4 | 10⁻⁴ | 10⁻³ |
| 5 | 10⁻⁵ | 10⁻⁴ |
| 6 | 10⁻⁶ | 10⁻⁵ (default) |
| 7 | 10⁻⁷ | 10⁻⁶ |
| 8 | 10⁻⁸ | 10⁻⁷ |

## Initial Guess Methods

### SAD (Superposition of Atomic Densities)

Default and recommended:

```
$rem
   SCF_GUESS          sad
$end
```

- Constructs density from atomic densities
- Usually converges well
- Fast and robust

### Core Hamiltonian

```
$rem
   SCF_GUESS          core
$end
```

- Uses core Hamiltonian diagonalization
- May help when SAD fails

### Read from Previous Job

```
$rem
   SCF_GUESS          read
$end
```

- Reads MOs from previous calculation
- Requires `mo_read` or same job directory

## Maximum SCF Cycles

```
$rem
   MAX_SCF_CYCLES     100
$end
```

Default is 50 cycles. Increase for difficult cases.

## Convergence Algorithms

### DIIS (Direct Inversion in Iterative Subspace)

Default algorithm for converging SCF:

```
$rem
   SCF_ALGORITHM      diis
$end
```

### GDM (Geometric Direct Minimization)

For difficult convergence:

```
$rem
   SCF_ALGORITHM      gdm
$end
```

- Direct minimization approach
- More robust for difficult systems
- Slower than DIIS for easy cases

### RCA (Relaxed Constrained Algorithm)

```
$rem
   SCF_ALGORITHM      rca
$end
```

### DM (Direct Minimization)

```
$rem
   SCF_ALGORITHM      dm
$end
```

## Level-Shifting

For oscillating SCF:

```
$rem
   SCF_LEVEL_SHIFT    200
$end
```

- Shifts virtual orbital energies
- Helps convergence for small gap systems
- Default: 0, typical: 100-500 (in 10⁻³ Hartree)

## Damping

```
$rem
   SCF_DAMPING        true
$end
```

- Damps density matrix changes
- Helps oscillating convergence

## Fractional Occupation (pFON)

For near-degenerate systems:

```
$rem
   SMOOTH_FIT         true
   PFON_SMOOTHING     true
$end
```

## MOM (Maximum Overlap Method)

For excited states or avoiding variational collapse:

```
$rem
   MOM_METHOD          1
$end
```

- Maintains orbital character
- Useful for excited state optimization

## Stability Analysis

Check for unstable solutions:

```
$rem
   STABILITY_ANALYSIS      follow
$end
```

| Value | Meaning |
|-------|---------|
| `check` | Check only |
| `follow` | Follow unstable direction to new minimum |

## SCF Failure Recovery Sequence

1. Increase MAX_SCF_CYCLES (100-200)
2. Try SCF_ALGORITHM = GDM
3. Add SCF_LEVEL_SHIFT (200-500)
4. Use SCF_DAMPING = true
5. Reduce SCF_CONVERGENCE to 5 temporarily
6. Check charge/multiplicity
7. Try different basis set

## Common SCF Problems

| Problem | Cause | Fix |
|---------|-------|-----|
| Oscillating | Small HOMO-LUMO gap | Level shift, damping |
| No convergence | Bad guess | GDM algorithm |
| Wrong solution | Unstable minimum | Stability analysis |
| Slow | Large system | Use RI methods |
| Divergence | Charge/mult wrong | Verify electron count |

## Open Shell Systems

For radicals:

```
$rem
   UNRESTRICTED       true
   SCF_GUESS          sad
$end
```

Broken symmetry solutions may require:

```
$rem
   UNRESTRICTED       true
   SCF_GUESS          mix
$end
```

## Troubleshooting Examples

### Difficult Convergence

```
$rem
   JOBTYPE            sp
   METHOD             b3lyp
   BASIS              6-31g*
   MAX_SCF_CYCLES     200
   SCF_ALGORITHM      gdm
   SCF_CONVERGENCE    5
$end
```

### Oscillating Convergence

```
$rem
   JOBTYPE            sp
   METHOD             b3lyp
   BASIS              6-31g*
   SCF_LEVEL_SHIFT    300
   SCF_DAMPING        true
$end
```

### Metal Complex

```
$rem
   JOBTYPE            sp
   METHOD             b3lyp
   BASIS              def2-tzvp
   UNRESTRICTED       true
   SCF_ALGORITHM      gdm
   MAX_SCF_CYCLES     150
$end
```