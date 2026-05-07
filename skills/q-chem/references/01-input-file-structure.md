# Input File Structure

## Input File Anatomy

A Q-Chem input file uses section-based format:

```
$comment
   Optional job description
$end

$molecule
   charge multiplicity
   atom_symbol  x  y  z
   ...
$end

$rem
   keyword       value
   ...
$end

$basis
   Optional custom basis set
$end
```

## Required Sections

| Section | Purpose | Required |
|---------|---------|----------|
| `$molecule` | Molecular geometry and charge/multiplicity | Yes |
| `$rem` | Job control keywords | Yes |
| `$comment` | Job description | No |
| `$basis` | Custom basis set specification | No |

## $molecule Section

Format: `charge multiplicity` on first line, then Cartesian coordinates:

```
$molecule
0 1
O    0.000000   0.000000   0.000000
H    0.000000   0.757160   0.586260
H    0.000000  -0.757160   0.586260
$end
```

### Charge and Multiplicity

| Multiplicity | Meaning | Electrons |
|-------------|---------|-----------|
| 1 | Singlet (all paired) | Even number |
| 2 | Doublet (one unpaired) | Odd number |
| 3 | Triplet | Odd number |

Format: `charge spin_multiplicity`

Examples:
```
0 1          # Neutral singlet (closed shell)
0 2          # Neutral doublet (radical)
-1 1         # Anion singlet
+1 2         # Cation doublet (open shell)
```

### Coordinate Units

Coordinates can be in Angstroms (default) or Bohr:

```
$molecule
0 1
...
$end

$rem
   INPUT_BOHR     true     # Use Bohr units
$end
```

## $rem Section

The `$rem` section controls all calculation parameters:

```
$rem
   JOBTYPE              sp
   METHOD               b3lyp
   BASIS                6-31g*
$end
```

### Essential $rem Keywords

| Keyword | Values | Purpose |
|---------|---------|---------|
| `JOBTYPE` | sp, opt, freq, rpath, ts | Type of calculation |
| `METHOD` | HF, B3LYP, MP2, CCSD(T) | Electronic structure method |
| `BASIS` | 6-31G*, cc-pVDZ, etc. | Basis set |
| `UNRESTRICTED` | true, false | Use unrestricted (UHF/UDFT) |
| `SCF_GUESS` | sad, core, read | Initial MO guess |

### Print Control

| Keyword | Values | Purpose |
|---------|---------|---------|
| `PRINT_INPUT` | true, false | Print input file in output |
| `GUI_PRINT` | 0-2 | Print additional info for IQmol |

## $basis Section

For custom basis sets:

```
$basis
****
O 1
S   3  1.00
   13.423000    0.0197700
   2.024000     0.1242300
   0.547000     0.4745800
****
$end
```

## Complete Example

```
$comment
Water molecule single point calculation with B3LYP/6-31G*
$end

$molecule
0 1
O    0.000000   0.000000   0.000000
H    0.000000   0.757160   0.586260
H    0.000000  -0.757160   0.586260
$end

$rem
   JOBTYPE              sp
   METHOD               b3lyp
   BASIS                6-31g*
   SCF_GUESS            sad
$end
```

## Running Q-Chem

```
qchem input.in output.out scratch_dir
```

Or with default scratch:

```
qchem input.in output.out
```

## Batch Jobs (Multiple Calculations)

Use `@@@` separator for multiple jobs:

```
$molecule
0 1
O  0.0  0.0  0.0
H  0.0  0.9  0.0
$end

$rem
   JOBTYPE          sp
   METHOD           b3lyp
   BASIS            6-31g*
$end

@@@

$molecule
0 1
O  0.0  0.0  0.0
H  0.0  0.9  0.0
$end

$rem
   JOBTYPE          freq
   METHOD           b3lyp
   BASIS            6-31g*
$end
```