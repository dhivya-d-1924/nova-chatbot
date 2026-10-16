// Create the shared admin account once, before any test runs (parallel projects would otherwise race to create it).
import { PASSWORD } from "./helpers";

export default async function globalSetup(config) {
  const base = config.projects[0].use.baseURL;
  const res = await fetch(`${base}/api/auth/register`, {
    method: "POST", headers: { "Content-Type": "application/json", Origin: base },
    body: JSON.stringify({ name: "Admin", email: "admin@e2e.test", password: PASSWORD }),
  });
  if (![201, 409].includes(res.status)) throw new Error(`could not create the e2e admin: ${res.status} ${await res.text()}`);
}
