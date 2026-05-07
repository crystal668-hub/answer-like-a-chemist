# Method and Basis Set Selection

## Method Hierarchy

```
Low accuracy ──────────────────────────────────── High accuracy
   │
   ├── Hartree-Fock (HF)
   │       │
   │       └── DFT (B3LYP, ωB97X-D, etc.)
   │               │
   │               ├── MP2 (Møller-Plesset)
   │               │       │
   │               │       └── CCSD(T) (gold standard)
   │               │
   │               └── DFT with larger basis
   │
   └── Higher accuracy with larger basis
```

## Hartree-Fock (HF)

- Foundation for all methods
- Includes exchange but NO correlation
- Fastest, scales as N⁴

**Use for:** Testing, initial guesses, large systems where correlation is minor.

```
$rem
   METHOD          hf
$end
```

## Density Functional Theory (DFT)

DFT includes both exchange and correlation through functionals.

### Popular Functionals

| Functional | Type | Notes |
|-----------|------|-------|
| B3LYP | Hybrid | Most widely used, good general-purpose |
| ωB97X-D | Range-separated hybrid | Good for non-covalent, includes dispersion |
| ωB97X-V | Range-separated hybrid | VV10 nonlocal correlation |
| M06-2X | Hybrid | Good for thermochemistry |
| PBE0 | Hybrid | General purpose |
| B97-D3 | GGA | Dispersion-corrected |
| SCAN | Meta-GGA | Modern non-empirical |
| r²SCAN | Meta-GGA | Regularized SCAN |

### Functional Selection Guide

| Use Case | Recommended Functional |
|----------|----------------------|
| General purpose | B3LYP |
| Non-covalent interactions | ωB97X-D, ωB97X-V |
| Thermochemistry | M06-2X |
| Ground state organic | B3LYP, M06-2X |
| Excited states | TDDFT with ωB97X-D |
| Charge transfer | ωB97X-D (range-separated) |
| Large systems | B97-D3, PBE |

### Q-Chem Unique Features

Q-Chem excels in:
- Range-separated hybrid functionals (ωB97 series)
- VV10 nonlocal correlation
- User-defined functionals
- DFT-D dispersion corrections

```
$rem
   METHOD              wB97X-D
   BASIS               def2-SVP
$end
```

## Wavefunction Methods

### MP2 (Møller-Plesset 2nd order)

- Includes electron correlation
- Scales as N⁵
- Good for medium systems (~50 atoms)

```
$rem
   METHOD          mp2
$end
```

### RI-MP2 (Resolution of Identity MP2)

- Faster than canonical MP2
- Uses auxiliary basis set

```
$rem
   METHOD              rimp2
   AUX_BASIS           rimp2-cc-pVDZ
$end
```

### CCSD(T) (Coupled Cluster)

- "Gold standard" for single reference
- Scales as N⁷
- Use for final energies on small systems

```
$rem
   METHOD          ccsd(t)
$end
```

## Basis Sets

### Pople Basis Sets (6-31G family)

| Basis | Description |
|-------|-------------|
| `6-31G` | Split-valence, double-zeta |
| `6-311G` | Triple-zeta valence |
| `6-31G*` | + d polarization on heavy atoms |
| `6-31G**` | + d on heavy, p on H |
| `6-311G**` | Triple-zeta + polarization |

### Correlation-Consistent (Dunning)

| Basis | Description |
|-------|-------------|
| `cc-pVDZ` | Double-zeta |
| `cc-pVTZ` | Triple-zeta |
| `cc-pVQZ` | Quadruple-zeta |
| `aug-cc-pVDZ` | + diffuse functions |

### Def2 (Karlsruhe)

| Basis | Description |
|-------|-------------|
| `def2-SVP` | Split-valence polarization |
| `def2-TZVP` | Triple-zeta valence polarization |
| `def2-TZVPP` | Triple-zeta with double polarization |
| `def2-QZVP` | Quadruple-zeta |

### Selection Guide

| System Size | Recommended Basis |
|------------|------------------|
| < 20 atoms | 6-311G** or cc-pVTZ |
| 20-50 atoms | 6-31G* or def2-SVP |
| 50-100 atoms | 6-31G or def2-SVP |
| > 100 atoms | 3-21G (screening) |

## Auxiliary Basis Sets

For RI methods:

```
$rem
   METHOD              rimp2
   BASIS               cc-pVDZ
   AUX_BASIS           rimp2-cc-pVDZ
$end
```

Common auxiliary basis naming: `rimp2-basis`, `rifit-basis`

## Effective Core Potentials (ECP)

For heavy elements:

```
$rem
   METHOD          b3lyp
   BASIS           lanl2dz
$end
```

Or use def2-ECP basis sets:
- `def2-TZVP` (includes ECP for heavy atoms)

## Open Shell Systems

For radicals, use unrestricted methods:

```
$rem
   METHOD          b3lyp
   UNRESTRICTED    true
$end
```

## Method + Basis Recommendations

| Purpose | Method | Basis | Notes |
|---------|--------|-------|-------|
| Geometry optimization | B3LYP | 6-31G* | Good accuracy |
| Frequency (ZPE, entropy) | B3LYP | 6-31G* | Match opt level |
| Single point energy | CCSD(T) | cc-pVTZ | High accuracy |
| Non-covalent | ωB97X-D | def2-TZVPP | Dispersion important |
| TS/barrier | M06-2X | 6-311+G** | Good for kinetics |

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| Using HF for geometry | Ignores correlation | Use DFT or MP2 |
| Mixing method and basis | Wrong level comparison | Consistent throughout |
| Too small basis | Basis set superposition error | Use polarization functions |
| Using DFT without dispersion | No dispersion by default | Use ωB97X-D or D3 correction |