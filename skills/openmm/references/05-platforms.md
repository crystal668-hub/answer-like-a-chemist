# Platforms

## Available Platforms

OpenMM includes five platforms:

| Platform | Description | Performance |
|----------|-------------|-------------|
| CUDA | NVIDIA GPUs | Fastest |
| HIP | AMD GPUs (ROCm) | Fast |
| OpenCL | Cross-platform GPU | Good |
| CPU | Multi-threaded CPU | Moderate |
| Reference | Single-threaded CPU | Slowest (validation) |

## Platform Selection

### Automatic (Default)

OpenMM automatically selects the fastest available platform:

```python
simulation = Simulation(topology, system, integrator)
```

### Environment Variable

```bash
export OPENMM_DEFAULT_PLATFORM=CUDA
```

### Explicit Selection

```python
platform = Platform.getPlatform('CUDA')
simulation = Simulation(topology, system, integrator, platform)
```

## CUDA Platform

For NVIDIA GPUs:

```python
platform = Platform.getPlatform('CUDA')
properties = {'Precision': 'mixed', 'DeviceIndex': '0'}
simulation = Simulation(topology, system, integrator, platform, properties)
```

### CUDA Properties

| Property | Options | Default |
|----------|---------|---------|
| `Precision` | single, mixed, double | mixed |
| `DeviceIndex` | GPU index (0, 1, etc.) | 0 |
| `UseCpuPme` | true, false | false |

### Multi-GPU

```python
properties = {'DeviceIndex': '0,1', 'Precision': 'mixed'}
```

## HIP Platform

For AMD GPUs (requires ROCm):

```python
platform = Platform.getPlatform('HIP')
properties = {'Precision': 'mixed'}
```

### HIP Properties

| Property | Options | Default |
|----------|---------|---------|
| `Precision` | single, mixed, double | mixed |
| `DeviceIndex` | GPU index | 0 |

## OpenCL Platform

Cross-platform GPU (NVIDIA, AMD, Intel):

```python
platform = Platform.getPlatform('OpenCL')
properties = {'Precision': 'mixed'}
```

### OpenCL Properties

| Property | Options | Default |
|----------|---------|---------|
| `Precision` | single, mixed, double | mixed |
| `DeviceIndex` | Device index | 0 |
| `PlatformIndex` | OpenCL platform | 0 |

## CPU Platform

Multi-threaded CPU execution:

```python
platform = Platform.getPlatform('CPU')
```

No special properties needed. Uses all available CPU threads.

## Reference Platform

Single-threaded CPU for validation:

```python
platform = Platform.getPlatform('Reference')
```

Use for: Testing, debugging, comparing to other codes.

## Precision Modes

| Mode | Description | Speed | Accuracy |
|------|-------------|-------|----------|
| single | 32-bit float | Fastest | Good for MD |
| mixed | 32-bit + 64-bit selected | Fast | Recommended |
| double | 64-bit float | Slowest | Highest accuracy |

**Recommended:** `mixed` for production MD.

## Determinism

For reproducible simulations:

```python
platform = Platform.getPlatform('CUDA')
properties = {'Precision': 'double', 'DeterministicForces': 'true'}
```

Or use Reference platform (always deterministic).

## Platform Selection Guide

| Hardware | Recommended Platform |
|----------|---------------------|
| NVIDIA GPU | CUDA (mixed precision) |
| AMD GPU (Linux) | HIP |
| AMD GPU (Windows) | OpenCL |
| Intel GPU | OpenCL |
| CPU-only | CPU |
| Validation/debug | Reference |

## Testing Platform Availability

```python
from openmm import Platform

for name in ['CUDA', 'OpenCL', 'HIP', 'CPU', 'Reference']:
    try:
        platform = Platform.getPlatform(name)
        print(f'{name}: available')
    except:
        print(f'{name}: not available')
```

Or run installation test:

```bash
python -m openmm.testInstallation
```

## Common Platform Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `CUDA platform not available` | No CUDA driver | Install NVIDIA driver |
| `OpenCL platform not available` | No OpenCL runtime | Install GPU driver |
| `HIP platform not available` | No ROCm | Install ROCm on Linux |
| `Device not found` | Wrong DeviceIndex | Check available GPUs |