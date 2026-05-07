# Geometries and Charges

## Charge and Multiplicity

The first line of `$molecule` specifies charge and spin multiplicity:

```
$molecule
charge multiplicity
atom x y z
...
$end
```

### Charge

Total charge of the molecule:
- `0` = neutral
- `-1` = anion
- `+1` or `1` = cation
- `-2` = dianion, etc.

### Multiplicity

Spin multiplicity (2S+1, where S is total spin):

| Multiplicity | Spin State | Unpaired Electrons |
|-------------|------------|-------------------|
| 1 | Singlet | 0 |
| 2 | Doublet | 1 |
| 3 | Triplet | 2 |
| 4 | Quartet | 3 |
| 5 | Quintet | 4 |

### Electron Count Verification

Verify charge and multiplicity are consistent:

```
Electrons = Z(sum of atoms) - charge
Multiplicity = 2S + 1
S = (N_alpha - N_beta) / 2
```

For even electrons: Multiplicity must be odd (1, 3, 5...)
For odd electrons: Multiplicity must be even (2, 4, 6...)

### Examples

```
0 1    # Neutral singlet (e.g., closed-shell organic molecule)
0 2    # Neutral doublet (e.g., neutral radical)
0 3    # Neutral triplet (e.g., O₂)
-1 1   # Anion singlet (e.g., closed-shell anion)
+1 2   # Cation doublet (e.g., radical cation)
-1 2   # Anion doublet (e.g., radical anion)
```

## Cartesian Coordinates

Standard format (Angstroms):

```
$molecule
0 1
O    0.000000   0.000000   0.000000
H    0.757160   0.586260   0.000000
H   -0.757160   0.586260   0.000000
$end
```

Format: `atom_symbol  x  y  z`

### Atomic Number Format

Can use atomic numbers instead of symbols:

```
$molecule
0 1
8    0.0    0.0    0.0
1    0.9    0.0    0.0
1   -0.9    0.0    0.0
$end
```

### Bohr Units

Use Bohr instead of Angstrom:

```
$molecule
0 1
...
$end

$rem
   INPUT_BOHR     true
$end
```

## Z-Matrix Format

Q-Chem supports Z-matrix coordinates:

```
$molecule
0 1
O
H  1  0.96
H  1  0.96  2  104.5
$end
```

Format:
- First atom: just the symbol
- Second atom: `symbol  ref_atom  bond_length`
- Third atom: `symbol  ref1  bond  ref2  angle`
- Later atoms: `symbol  ref1  bond  ref2  angle  ref3  dihedral`

## Reading Coordinates from File

Read coordinates from previous calculation:

```
$molecule
read
$end
```

Read from specific file:

```
$molecule
read geometry_file.xyz
$end
```

## XYZ File Format

Q-Chem can read standard XYZ files:

```
3
Water molecule
O    0.000000   0.000000   0.000000
H    0.757160   0.586260   0.000000
H   -0.757160   0.586260   0.000000
```

## Symmetry

Q-Chem can use molecular symmetry:

```
$rem
   SYMMETRY         true
   SYM_IGNORE       false
$end
```

Symmetry can speed up calculations but may cause issues for optimizations.

### Disable Symmetry

```
$rem
   SYMMETRY         false
   SYM_IGNORE       true
$end
```

Recommended for:
- Geometry optimizations
- Transition state searches
- When convergence is difficult

## Fragment Calculations

For BSSE calculations or fragment-based methods:

```
$molecule
0 1 0 1 0 1
--
O  0.0  0.0  0.0
H  0.9  0.0  0.0
--
O  3.0  0.0  0.0
H  3.9  0.0  0.0
$end
```

Format: `total_charge total_mult frag_charge frag_mult ...`

## Frozen Atoms

Freeze specific atoms during optimization:

```
$rem
   JOBTYPE          opt
$end

$molecule
0 1
O  0.0  0.0  0.0  0
H  0.9  0.0  0.0  1
H -0.9  0.0  0.0  1
$end
```

Column 5: `0` = freeze, `1` = optimize

## Constraints

Apply constraints during optimization:

``
$rem
   JOBTYPE          opt
   CONSTRAINED_OPT  true
$end

$constraint
freeze bond 1 2
$end
```

## Geometry Quality

Before starting calculations:
1. Verify bond lengths are reasonable
2. Check angles are physically meaningful
3. No overlapping atoms
4. No extreme distortions

### Common Geometry Errors

| Error | Cause | Fix |
|-------|-------|-----|
| Overlapping atoms | Bad coordinates | Separate atoms |
| Extreme angles | Z-matrix error | Check reference atoms |
| Wrong connectivity | Z-matrix indices | Verify atom numbering |
| No convergence | Far from equilibrium | Pre-optimize at lower level |