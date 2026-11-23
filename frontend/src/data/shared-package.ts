/** 只读共享包：授权、凭据、读取与审计的数据契约。 */

export type SharedPackageSummary = {
  id: number
  package_name: string
  description: string
  owner_dept: string
  source_module: string
  source_ref: string
  data_scope: string[]
  dimensions: string[]
  data_version: number
  status: string
  created_at: string
  sample_count?: number
  region_counts?: Record<string, number>
  samples?: SharedSample[]
  grants?: SharedGrant[]
  read_count?: number
  provenance?: Record<string, string | number>
  scope?: { regions: string[]; dimensions: string[] }
}

export type SharedSample = {
  id: number
  sample_key: string
  region: string
  dimension_key: string
  event_name: string
  measure: number
  occurred_at: string
  data_version: number
}

export type SharedGrant = {
  id: number
  package_id: number
  recipient_dept: string
  regions: string[]
  dimensions: string[]
  grant_version: number
  status: string
  issued_by: string
  issued_at: string
  revoked_by?: string | null
  revoked_at?: string | null
}

export type SharedCredential = {
  id: number
  package_id: number
  grant_id: number
  recipient_dept: string
  regions: string[]
  dimensions: string[]
  grant_version: number
  status: string
  issued_at: string
  revoked_at?: string | null
  token?: string
  token_masked?: string
}

export type SharedAggregate = {
  region: string
  dimension_key: string
  sample_count: number
  measure_sum: number
}

export type AuthSnapshot = {
  time?: string
  status: string
  grant_version: number | null
  package_version: number | null
  scope: string[]
}

export type AuthTrace = { request: AuthSnapshot; return: AuthSnapshot }

export type SharedReadResult = {
  ok: boolean
  read_id: string
  outcome: string
  message?: string
  package_id?: number
  package_name?: string
  delivered_count: number
  delivered_regions?: string[]
  auth: AuthTrace
  scope?: { regions: string[]; dimensions: string[] }
  details?: SharedSample[]
  events?: SharedSample[]
  aggregates?: SharedAggregate[]
  event_count?: number
  verification?: {
    delivered_hash?: string
    request_package_version?: number
    return_package_version?: number
    recomputed_hash?: string
    hash_matches?: boolean
    current_package_version?: number
    version_matches?: boolean
    delivered_region_count?: number
  }
}

export type SharedReadRecord = {
  read_id: string
  package_id: number
  grant_id: number | null
  credential_id: number | null
  recipient_dept: string
  operation: string
  request_time: string
  request_auth_status: string
  request_grant_version: number | null
  request_package_version: number | null
  request_scope: string[]
  return_time: string | null
  return_auth_status: string
  return_grant_version: number | null
  return_package_version: number | null
  return_scope: string[]
  outcome: string
  delivered_count: number
  delivered_regions: string[]
  delivered_sample_ids: number[]
  delivered_hash: string
  delivered_rows: SharedSample[]
  verification?: Record<string, unknown>
  auth?: AuthTrace
}

export type AvailablePackage = {
  package: SharedPackageSummary
  credential: SharedCredential
  scope: { regions: string[]; dimensions: string[] }
  aggregates: SharedAggregate[]
}
