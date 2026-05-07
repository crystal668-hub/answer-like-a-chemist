# Integrators

## Integrator Selection

```
Stochastic (thermostatted) ───────────────────── Deterministic
    │
    ├── LangevinMiddle (recommended for NVT)
    │       │
    │       ├── LangevinIntegrator (older)
    │       │
    │       ├── BrownianIntegrator (high friction)
    │       │
    │       └── NoseHooverIntegrator (NPT)
    │
    └── VerletIntegrator (NVE, no thermostat)
```

## Langevin Integrators

### LangevinMiddleIntegrator (Recommended)

Best for constant-temperature (NVT) simulations:

```python
integrator = LangevinMiddleIntegrator(300*kelvin, 1/picosecond, 0.004*picoseconds)
```

| Parameter | Value | Meaning |
|-----------|-------|---------|
| Temperature | 300*kelvin | Target temperature |
| Friction | 1/picosecond | Collision rate |
| Timestep | 0.004*picoseconds | Integration step (4 fs) |

### LangevinIntegrator

Older Langevin integrator:

```python
integrator = LangevinIntegrator(300*kelvin, 1/picosecond, 0.002*picoseconds)
```

Use LangevinMiddleIntegrator instead for better accuracy.

### VariableLangevinIntegrator

Adaptive timestep:

```python
integrator = VariableLangevinIntegrator(300*kelvin, 1/picosecond, 0.001*picoseconds)
integrator.setMaximumStepSize(0.002*picoseconds)
```

## Verlet Integrator

Constant-energy (NVE) simulation:

```python
integrator = VerletIntegrator(0.002*picoseconds)
```

No temperature control — energy should be conserved.

## Nose-Hoover Integrator

Chain thermostat for better temperature control:

```python
integrator = NoseHooverIntegrator(300*kelvin, 0.004*picoseconds)
```

## Brownian Integrator

Overdamped dynamics (high friction limit):

```python
integrator = BrownianIntegrator(300*kelvin, 1/picosecond, 0.001*picoseconds)
```

Use for: Diffusive processes, high-temperature annealing.

## Barostats (Pressure Control)

### Monte Carlo Barostat

```python
system.addForce(MonteCarloBarostat(1*bar, 300*kelvin))
```

| Parameter | Meaning |
|-----------|---------|
| 1*bar | Target pressure |
| 300*kelvin | Temperature (must match integrator) |

### Anisotropic Barostat

```python
system.addForce(MonteCarloAnisotropicBarostat(
    (1*bar, 1*bar, 1*bar), 300*kelvin, False, False, True))
```

### Membrane Barostat

```python
system.addForce(MonteCarloMembraneBarostat(1*bar, 300*kelvin,
    MonteCarloMembraneBarostat.XYIsotropic, MonteCarloMembraneBarostat.ZFree))
```

## Drude Integrators

For polarizable force fields (CHARMM Drude):

```python
integrator = DrudeLangevinIntegrator(300*kelvin, 1/picosecond,
    0.004*picoseconds, 1*kelvin, 10/picosecond)
```

## Integrator Parameters

### Changing Temperature

```python
integrator.setTemperature(350*kelvin)
```

### Changing Timestep

```python
integrator.setStepSize(0.002*picoseconds)
```

## Recommended Integrator Settings

| Simulation Type | Integrator | Timestep | Notes |
|-----------------|------------|----------|-------|
| Standard MD (NVT) | LangevinMiddle | 4 fs | With HBonds constraints |
| NPT MD | LangevinMiddle + MonteCarloBarostat | 4 fs | Add barostat to system |
| NVE | Verlet | 2 fs | No constraints recommended |
| Equilibration | LangevinMiddle | 2 fs | Start slow |
| Production | LangevinMiddle | 4 fs | Standard |
| High friction | Brownian | 1 fs | Diffusive regime |
| Polarizable | DrudeLangevin | 1 fs | CHARMM Drude |

## Thermostat Comparison

| Thermostat | Advantages | Disadvantages |
|------------|------------|---------------|
| Langevin | Simple, robust | Perturbs dynamics |
| Nose-Hoover | Better sampling, realistic dynamics | More complex |
| Andersen | Simple stochastic | Less realistic |

## Timestep Guidelines

| Timestep | Constraints Needed |
|----------|--------------------|
| 4 fs | HBonds (or AllBonds) |
| 2 fs | HBonds |
| 1 fs | None possible |

Heavy hydrogen (mass repartitioning) allows 4-5 fs with HBonds:
```python
modeller.addHydrogens(forcefield, variant='heavy')
```