# Container Proxy and DNS Follow-up

- Date: 2026-09-11, Asia/Shanghai.
- Scope: recurring Qwen API DNS failures and shared container network setup.
- Status: implementation and network verification complete; upstream DNS
  NODATA mechanism identified, upstream server internals remain unobservable.
- Baseline: source `c91a526`, OpenClaw 2026.6.9, image
  `sha256:5b968aebe2b3e5cb2678e9b3a0d3fffd6fc0cc0314b204021ba9c48036a4fa96`.

## Incident and DNS Evidence

The 21:32:52-21:48:47 formal run
`verifier-grounded-property-calculation-easy-qwen3-8-flash-20260911-213250`
contained three questions in each skills group. All six exhausted their retry
budget. The 24 attempts included 22 provider connection errors with
`ENOTFOUND agent-team-api.myrimate.cn` and two initial response timeouts. There
were no container-name conflicts; all 24 containers were removed.

The API hostname is a CNAME for
`b0bc487558044a2ea6964e306953dee8.vip1.huaweicloudwaf.com`. Live tests identify
their macOS log name hashes as `b010664c` and `b351e988`, respectively.
The host resolver service uses en1 and upstreams `202.38.64.18` and
`202.38.64.56`; Docker's `192.168.65.7` forwards through the host resolver.

The unified log records the following sequence on 2026-09-11:

1. At 21:01:28.700 the upstream replied to a direct WAF-hostname A query with
   four IPv4 addresses and TTL 10 seconds.
2. At 21:06:19.512, query `Q3829` sent a WAF-hostname A request through en1.
3. At 21:06:19.519 the upstream replied with DNS flags `0x8180` (`NOERROR`),
   counts `1/0/1/0`, and an SOA with remaining TTL 295 and minimum TTL 300.
   This is NODATA: a successful DNS response containing no A answer. It is
   not a socket timeout or a Docker-generated NXDOMAIN response.
4. macOS delivered `type: A, rdata: <none>` to Docker immediately and returned
   the negative result repeatedly during 21:06-21:12. Resolution was positive
   again in the 21:16 observations.
5. During the later run, A results were positive in 21:33-21:45, then switched
   to empty in 21:46-21:48. At 21:48:44.040, request `R1123317` followed the
   API CNAME and received `type: A, rdata: <none>` for the WAF target.

These records establish upstream negative answers followed by host negative
caching/forwarding as the observed failure mechanism. The upstream address is
privacy-masked in historical logs; its exact identity and why it returned
NODATA cannot be proved from this host. Recursive-cache inconsistency or an
upstream authoritative response remain possible, not established conclusions.
AAAA NODATA alone is normal for this IPv4 endpoint and is not the failure.

Follow-up sampled host lookup and both upstreams 24 times over two minutes:
no lookup exceptions occurred. Direct UDP/TCP A queries and an independent
`1.1.1.1` query returned four IPv4 addresses. This does not erase the historical
failure evidence or establish that campus DNS is permanently repaired. No
system DNS servers, proxy listener binding, or hosts file were changed.

To retrieve the decisive historical evidence:

```sh
/usr/bin/log show --start '2026-09-11 21:06:18' --end '2026-09-11 21:06:20' \
  --style compact --info \
  --predicate 'process == "mDNSResponder" AND eventMessage CONTAINS "Q3829"'
```

## Shared Network Fix

The earlier preflight called `build_openclaw_subprocess_env`, which discovered
the macOS system proxy at `127.0.0.1:7892`, and skipped DNS validation. The
Docker runner used only process environment and `.env`; neither supplied those
proxy values. Its Linux wrapper could not rediscover macOS settings. Therefore
the preflight assumed proxy routing while model requests used direct DNS.

`container_network` now owns immutable per-invocation proxy variables and
network mode. Process environment overrides `.env`; system settings fill absent
HTTP/S proxies. macOS loopback proxies become `host.docker.internal:7892`, which
is reachable from Docker Desktop even though CatCore listens on host loopback.
The original `127.0.0.1:7892` address inside the container returned
`ECONNREFUSED`; the translated address succeeded without changing CatCore.

CLI preflight and runner instances receive the same object through service
composition. All retries retain it. Per-attempt and run manifests record the
same redacted network data. Both variable cases are normalized consistently,
including `NO_PROXY`; proxy credentials remain absent from reports.

The DNS-only probe is replaced by an unauthenticated GET using the pinned
image's original OpenClaw `buildGuardedModelFetch`, including explicit transport
settings. Proxy paths are tested. DNS/TCP/TLS errors, proxy authentication failure
(407), and 5xx responses fail startup. Other HTTP responses establish network
reachability only. Providers with no explicit endpoint still record a skipped
check. This probe does not assert model availability or predict later outages.
The probe script is supplied by the host; no image rebuild is required.

## Verification

- `uv run pytest tests -q`: 701 passed, 9 skipped, 124 subtests passed. An earlier
  full run had one transient external replay-lock failure; its isolated rerun
  and the subsequent full run passed.
- Network tests cover system proxy inheritance, macOS address translation,
  Linux host networking, lowercase precedence, credentials, redaction, frozen
  application, and process-over-dotenv precedence.
- Runner regression checks actual `ContainerAttemptSpec` proxy variables,
  network mode, and persisted metadata against the supplied configuration.
- CLI regression injects a failed proxy connection and verifies startup fails
  before scheduling or per-record materialization.
- Real preflight through `host.docker.internal:7892` returned HTTP 404 from the
  configured API base path; `/v1/models` returned HTTP 401.
- Real negative control using `host.docker.internal:1` failed with
  `ECONNREFUSED`; it did not silently fall back to a direct request.
- A minimal authenticated `qwen3.8-flash` Responses request through the same
  configuration and OpenClaw transport returned HTTP 200, status `completed`,
  and text `OK`. A separate streaming request also returned HTTP 200,
  `response.completed`, and `OK` (7,988 response bytes).
- A temporary full runner attempt at
  `state/benchmark-runs/temporary/infra-network/qwen3-8-flash/infra-network-qwen3-8-flash-20260911-234000`
  recorded identical preflight/attempt networking and no DNS error, but hit its
  90-second model deadline. It is not a successful scored benchmark result.
- A second temporary run with suffix `20260911-234500`, thinking disabled,
  and a 120-second model budget likewise timed out. Its persisted preflight and
  attempt network configurations matched exactly and contained no DNS error.
  Full benchmark completion remains unverified; the successful minimal
  authenticated requests validate transport, not the full prompt/tool workflow.
- Original formal run artifacts were retained unchanged.
