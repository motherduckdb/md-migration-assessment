// Source-specific wording. The panels and SQL are shared across adapters;
// only the vocabulary changes with meta.collections.source_kind.
import { createContext, createElement, useContext, type ReactNode } from 'react';
import { S } from './format';

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

type CollectionRow = Record<string, unknown>;
type VendorValue = { vendor: Vendor; row: CollectionRow };

export const VendorContext = createContext<VendorValue | null>(null);

// Picks the vocabulary from the collection row, which the Dive loads once at the top.
export function VendorProvider({ row, children }: { row: CollectionRow; children: ReactNode }) {
  const vendor = S(row.source_kind) === 'databricks' ? DATABRICKS : SNOWFLAKE;
  return createElement(VendorContext.Provider, { value: { vendor, row } }, children);
}

function useVendorValue(): VendorValue {
  const v = useContext(VendorContext);
  if (!v) throw new Error('useVendor must be used inside <VendorProvider>');
  return v;
}

export function useVendor(): Vendor {
  return useVendorValue().vendor;
}

// The single collection row, for panels that need more than the vocabulary.
export function useCollectionRow(): CollectionRow {
  return useVendorValue().row;
}
