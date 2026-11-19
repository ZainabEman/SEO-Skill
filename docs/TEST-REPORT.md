# Test report

Run `./setup.sh` from a fresh Cloud checkout to install dependencies and run the offline suite. Tests use synthetic evidence, injected website and provider responses, temporary ledgers and generated documents. The suite covers HTML, redirects, robots, sitemaps, evidence pointers, cost approvals, concurrency, cache, scoring and document integrity.

For deployment acceptance, run the skill with a permitted public URL and a zero-credit scope. Verify repository discovery, a successful model-backed invocation, provider connection and report delivery. Repeat in a second authorized workspace account. Store results and any client documents only in approved private storage.
