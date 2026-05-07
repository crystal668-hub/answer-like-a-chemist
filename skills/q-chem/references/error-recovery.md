# Error Recovery

## Diagnostic First Steps

Read the Q-Chem output file from the **end** (where the job failed).

## Input File Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `Missing $end` | Section not closed | Add `$end` to all sections |
| `Invalid $rem keyword` | Unknown keyword | Check Q-Chem documentation |
| `Charge/multiplicity error` | Inconsistent values | Verify electron count |
| `Geometry error` | Bad coordinate format | Check atom format |
| `No input` | Empty input file | Add $molecule and $rem |

### Input Recovery

1. Check all sections have `$end`
2. Verify keyword syntax (no spaces in values usually)
3. Check charge/multiplicity match electron count
4. Validate geometry format

## SCF Failures

| Error | Cause | Fix |
|-------|-------|-----|
| `SCF failed to converge` | Did not reach convergence | Use GDM, increase cycles |
| `Bad SCF guess` | Poor initial orbitals | Try SAD or READ |
| `Unstable solution` | Wavefunction instability | Run stability analysis |
| `Oscillating` | Small HOMO-LUMO gap | Use level shift, damping |

### SCF Recovery Sequence

1. Increase `MAX_SCF_CYCLES` to 100-200
2. Change `SCF_ALGORITHM` to `GDM`
3. Add `SCF_LEVEL_SHIFT` (200-500)
4. Enable `SCF_DAMPING`
5. Reduce `SCF_CONVERGENCE` temporarily
6. Verify charge/multiplicity

### Difficult SCF Example

```
$rem
   MAX_SCF_CYCLES     200
   SCF_ALGORITHM      gdm
   SCF_CONVERGENCE    5
   SCF_LEVEL_SHIFT    300
$end
```

## Optimization Failures

| Error | Cause | Fix |
|-------|-------|-----|
| `Optimization failed` | Bad starting geometry | Use better structure |
| `Too many steps` | Max cycles exceeded | Increase MAX_OPT_CYCLES |
| `TS not found` | Poor TS guess | Use scan or different guess |
| `Geometry unreasonable` | Bad coordinates | Check initial structure |

### Optimization Recovery

1. Check last geometry in output
2. Verify forces are decreasing
3. Increase `MAX_OPT_CYCLES`
4. Reduce optimization thresholds
5. Use better starting geometry

### Opt Settings for Difficult Cases

```
$rem
   JOBTYPE           opt
   MAX_OPT_CYCLES    100
   OPT_TOL_GRAD      200
   SCF_ALGORITHM     gdm
$end
```

## Frequency Failures

| Error | Cause | Fix |
|-------|-------|-----|
| `Freq job failed` | SCF issues | Fix SCF first |
| `Imaginary frequency` | Not a minimum | If expected TS, correct |
| `No imaginary freq` | Not a TS | Try different TS guess |

### Frequency Recovery

1. Confirm geometry is optimized
2. Check all SCF converged
3. Run at same level as optimization

## Runtime Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `Out of memory` | MEM_TOTAL too low | Increase memory |
| `Scratch full` | Disk exhausted | Clean scratch or increase |
| `Segmentation fault` | Input error or bug | Simplify input |
| `File not found` | Missing file | Check file paths |
| `Timeout` | Walltime exceeded | Restart from last geometry |

## Solvent Model Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `Unknown solvent` | Wrong solvent name | Use correct name |
| `PCM failed` | Convergence issue | Try SMD instead |

## MPI/Parallel Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `MPI initialization failed` | MPI config issue | Check MPI setup |
| `Node failure` | Hardware issue | Report to admin |
| `Communication error` | Network issue | Check network |

## Error Pattern Recognition

### No Output Generated

- Input file syntax error
- Missing required sections
- Check for `$end` on all sections

### Partial Output Then Stop

- SCF convergence failure
- Memory exhaustion
- Check last section printed

### Job Runs Forever

- SCF oscillating
- Optimization cycling
- Add convergence aids

## Recovery Workflow

```
1. Job fails
   ↓
2. Read error from end of output
   ↓
3. Classify: Input / SCF / Optimization / Runtime
   ↓
4. For input: validate format, check sections
   ↓
5. For SCF: try GDM, level shift, damping
   ↓
6. For optimization: check geometry, increase cycles
   ↓
7. For runtime: check memory, scratch, paths
   ↓
8. Test fix with smaller system
   ↓
9. Resubmit corrected job
```

## Useful Debugging Keywords

```
$rem
   PRINT_INPUT       true
   PRINT_BASIS       true
   SCF_PRINT         3
$end
```

## Getting Help

1. Check Q-Chem manual
2. Search for error message
3. Simplify input to isolate problem
4. Contact support with:
   - Input file
   - Output file
   - Error description