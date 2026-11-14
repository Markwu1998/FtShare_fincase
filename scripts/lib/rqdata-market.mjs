import { execFile } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const run = promisify(execFile);
const script = name => fileURLToPath(new URL(name, import.meta.url));

export async function marketProvider(env = process.env) {
  if (!env.MARKET_DATA_CONFIG_FILE) return 'RQData';
  const { provider } = JSON.parse(await readFile(env.MARKET_DATA_CONFIG_FILE, 'utf8'));
  if (!['FTShare', 'RQData'].includes(provider)) throw new Error('Unsupported market data provider');
  return provider;
}

export async function rqdataMarket(request, env = process.env) {
  const executable = env.RQDATA_PYTHON || 'python';
  const args = [script('rqdata-market.py'), JSON.stringify(request)];
  if (!env.RQDATAC2_CONF && !env.RQDATAC_CONF && env.RQDATA_CREDENTIAL_FILE) {
    throw new Error('Load the local RQData credential with scripts/rqdata.ps1 before collecting');
  }
  try {
    const childEnv = { ...env };
    delete childEnv.FTSHARE_API_KEY;
    delete childEnv.FTSHARE_API_KEY_FILE;
    const { stdout } = await run(executable, args, { env: childEnv, maxBuffer: 48 * 1024 * 1024, timeout: 240000, windowsHide: true });
    const result = JSON.parse(stdout);
    if (result.error) throw new Error(result.error);
    return result;
  } catch (error) {
    // SDK and child-process errors can contain connection details.
    const code = ['authentication', 'query_failed', 'sdk_missing'].includes(error.message) ? error.message : 'bridge_failed';
    throw Object.assign(new Error('RQData: ' + code), { code });
  }
}
