// Source-specific wording. The panels and SQL are shared across adapters;
// only the vocabulary changes with meta.collections.source_kind.
import { useSQLQuery } from '@motherduck/react-sql-query';
import { S } from './format';
import * as Q from './queries';

export type Vendor = {
  name: string;
  account: string;
  credits: string;
  systemObjects: string;
  storageNote: string;
  spendNote: string;
};

const SNOWFLAKE: Vendor = {
  name: 'Snowflake',
  account: 'Snowflake account',
  credits: 'Credits are Snowflake credits (no rate is recorded, so no dollar figure is implied)',
  systemObjects: 'show Snowflake system objects (SNOWFLAKE shared database)',
  storageNote: 'Active vs time-travel vs fail-safe bytes per database (user objects unless the system-objects toggle is on). Fail-safe is retained by Snowflake and disappears on migration; it is stacked separately so it is never folded into the migrated total.',
  spendNote: 'Daily Snowflake credits per warehouse (compute + cloud services) over the history window.',
};

const DATABRICKS: Vendor = {
  name: 'Databricks',
  account: 'Databricks workspace',
  credits: 'Credits are Databricks DBUs from system.billing (list price is kept in report.spend_profile.list_cost, never the negotiated rate)',
  systemObjects: 'show Databricks system objects (system, samples, hive_metastore catalogs)',
  storageNote: 'Current Delta/Iceberg table size per catalog from DESCRIBE DETAIL (user objects unless the system-objects toggle is on). Older versions kept for time travel are not split out, so only active bytes are shown.',
  spendNote: 'Daily DBUs per SQL warehouse, or per product (jobs, pipelines, serverless, model serving) when no warehouse applies, over the history window.',
};

export function useVendor(): Vendor {
  const coll = useSQLQuery(Q.collection());
  const c = (Array.isArray(coll.data) ? coll.data : [])[0] ?? {};
  return S(c.source_kind) === 'databricks' ? DATABRICKS : SNOWFLAKE;
}
