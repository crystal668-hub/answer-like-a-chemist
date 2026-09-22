import { readdir } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const { model, request } = JSON.parse(Buffer.concat(chunks).toString());
const finish = (result) => {
  process.stdout.write(JSON.stringify(result));
  process.exit(0);
};
setTimeout(() => finish({ status: "failed", code: "ETIMEOUT" }), 15000);
try {
  const root = "/usr/local/lib/node_modules/openclaw/dist/";
  const files = await readdir(root);
  const load = async (prefix) => {
    const candidates = files.filter((name) => name.startsWith(prefix) && (name.endsWith(".mjs") || name.endsWith(".js")));
    const file = candidates.find((name) => name.endsWith(".mjs")) || candidates.find((name) => name.endsWith(".js"));
    if (!file) throw Object.assign(new Error(), { code: "OPENCLAW_TRANSPORT_UNAVAILABLE" });
    return import(pathToFileURL(root + file));
  };
  const { g: buildGuardedModelFetch } = await load("openai-transport-stream-");
  const { n: attachModelProviderRequestTransport } = await load("provider-request-config-");
  const prepared = attachModelProviderRequestTransport(model, request);
  const response = await buildGuardedModelFetch(prepared, 10000)(model.baseUrl, { method: "GET" });
  await response.body?.cancel();
  const status = response.status === 407 || response.status >= 500 ? "failed" : "ready";
  finish({ status, http_status: response.status, ...(status === "failed" ? { code: "HTTP_UNAVAILABLE" } : {}) });
} catch (error) {
  finish({ status: "failed", code: error.code || error.cause?.code || error.name || "TRANSPORT_ERROR" });
}
