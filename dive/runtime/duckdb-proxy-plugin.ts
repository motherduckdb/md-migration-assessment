/**
 * Dev-only Vite plugin: answers the dashboard's `api/query` and `api/info`
 * endpoints with Node DuckDB over a LOCAL collection, so the Dive source can be
 * previewed with hot reload and no MotherDuck connection.
 *
 *   ASSESSMENT_DB=../assessment.duckdb npm run dev
 *
 * The file is attached READ_ONLY under the alias the Dive's REQUIRED_DATABASES
 * declares (`assessment`), matching what `md-assess dashboard` does in Python.
 * This plugin never runs in the production bundle (apply: 'serve').
 */
import type { Plugin } from 'vite';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { resolve } from 'node:path';
import { existsSync } from 'node:fs';

const ALIAS = 'assessment';

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((res, rej) => {
    let data = '';
    req.on('data', (c: Buffer) => (data += c.toString()));
    req.on('end', () => res(data));
    req.on('error', rej);
  });
}

function sendJson(res: ServerResponse, status: number, body: unknown) {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body));
}

export default function duckdbProxy(): Plugin {
  return {
    name: 'md-assess-local-duckdb-proxy',
    apply: 'serve',
    configureServer(server) {
      const dbPath = process.env.ASSESSMENT_DB
        ? resolve(process.env.ASSESSMENT_DB)
        : resolve(import.meta.dirname, '..', '..', 'assessment.duckdb');
      if (!existsSync(dbPath)) {
        server.config.logger.warn(
          `[md-assess] no collection at ${dbPath}; set ASSESSMENT_DB=path/to/assessment.duckdb`,
        );
      }

      let ready: Promise<any> | null = null;
      const connect = () => {
        if (!ready) {
          ready = (async () => {
            const { DuckDBInstance } = await import('@duckdb/node-api');
            const instance = await DuckDBInstance.create(':memory:');
            const conn = await instance.connect();
            await conn.run(`ATTACH '${dbPath.replace(/'/g, "''")}' AS ${ALIAS} (READ_ONLY)`);
            await conn.run('SET enable_external_access = false');
            return conn;
          })();
        }
        return ready;
      };
      connect().catch((e) => server.config.logger.error(`[md-assess] ${e.message}`));

      server.middlewares.use(async (req, res, next) => {
        const url = (req.url ?? '').split('?')[0];
        if (url === '/api/info' && req.method === 'GET') {
          sendJson(res, 200, { mode: 'local', database: dbPath, alias: ALIAS });
          return;
        }
        if (url === '/api/query' && req.method === 'POST') {
          try {
            const { sql } = JSON.parse(await readBody(req));
            if (typeof sql !== 'string' || !sql.trim()) {
              sendJson(res, 400, { error: 'missing sql' });
              return;
            }
            const conn = await connect();
            const reader = await conn.runAndReadAll(sql);
            sendJson(res, 200, { data: reader.getRowObjectsJson() });
          } catch (err: any) {
            sendJson(res, 500, { error: err?.message ?? String(err) });
          }
          return;
        }
        next();
      });
    },
  };
}
