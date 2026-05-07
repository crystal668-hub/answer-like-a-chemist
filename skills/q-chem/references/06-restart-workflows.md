# Restart and Multi-Step Workflows

## Reading Molecular Orbitals

Continue from previous calculation by reading MOs:

```
$rem
   JOBTYPE          sp
   METHOD           b3lyp
   BASIS            6-31g*
   SCF_GUESS        read
$end
```

Q-Chem reads from the scratch directory of the previous job.

### Specify Previous Job

```
$rem
   SCF_GUESS        read
   INPUT_READ       previous_job.out
$end
```

## Batch Jobs

Run multiple calculations in one input file using `@@@` separator:

```
$molecule
0 1
O  0.0  0.0  0.0
H  0.9  0.0  0.0
H -0.9  0.0  0.0
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

### Workflow Explanation

1. First job: Geometry optimization
2. `@@@` separator starts new job
3. `$molecule read` uses geometry from previous job
4. Second job: Frequency calculation
5. Third job: High-level single point

## Reading Geometry

After any job, use `read` to get the optimized geometry:

```
$molecule
read
$end
```

## Multi-Step Optimization Workflow

### Optimization → Frequency → High-Level SP

```
$comment
Step 1: Geometry optimization
$end

$molecule
0 1
...initial geometry...
$end

$rem
   JOBTYPE          opt
   METHOD           b3lyp
   BASIS            6-31g*
   GEOMETRY_OPTIMIZE true
$end

@@@

$comment
Step 2: Frequency calculation
$end

$molecule
read
$end

$rem
   JOBTYPE          freq
   METHOD           b3lyp
   BASIS            6-31g*
$end

@@@

$comment
Step 3: High-level single point
$end

$molecule
read
$end

$rem
   JOBTYPE          sp
   METHOD           ccsd(t)
   BASIS            cc-pVTZ
$end
```

## Plot Files

Q-Chem generates various output files:

| File | Content |
|------|---------|
| `.out` | Main output |
| `.plot` | Molden-format orbitals |
| `.fchk` | Formatted checkpoint (like Gaussian) |
| `.esp` | Electrostatic potential grid |

### Generate Plot Files

```
$rem
   MAKE_PLOT_FILES    true
   PLOT_ORBITALS      true
   PLOT_SPIN_DENSITY  true
$end
```

### Molden File

```
$rem
   MOLDEN_FORMAT      true
$end
```

Generates `.plot` file readable by Molden.

## Formatted Checkpoint

```
$rem
   FCHK               true
$end
```

Creates `.fchk` file similar to Gaussian format.

## Saving Scratch Files

Q-Chem scratch directory contains:
- `132.0` - MO coefficients
- `53.0` - Two-electron integrals
- `30.0` - One-electron integrals

To preserve for restart:

```
$rem
   SAVE_SCRATCH       true
$end
```

## Restart After Walltime

If job exceeds walltime, restart from last step:

1. Check output for last completed geometry
2. Extract geometry from output
3. Start new job with that geometry

Or use batch job approach with checkpoint reading.

## Common Multi-Step Workflows

### Complete Thermochemistry

```
opt (B3LYP/6-31G*)
→ freq (B3LYP/6-31G*)
→ sp (MP2/cc-pVTZ)
```

### Excited State Study

```
opt (ground state)
→ tddft (excited states)
→ opt (specific excited state)
```

### Reaction Mechanism

```
opt (reactant)
→ ts (transition state)
→ freq (TS, verify one imaginary)
→ rpath (IRC)
→ opt (product)
```

## Error Handling in Batch Jobs

If one job fails, subsequent jobs don't run.

Check output file for:
- `Q-Chem execution completed`
- `Have a nice day.`

Indicates successful completion.

## Debugging Batch Jobs

Run jobs separately first:

```
qchem step1.in step1.out scratch1
qchem step2.in step2.out scratch2
```

Then combine once each step works.