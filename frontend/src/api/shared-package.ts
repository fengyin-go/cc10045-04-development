/** 只读共享包请求封装：拥有方管理与接收方读取。 */
import { request } from './client'
import type {
  AvailablePackage,
  SharedCredential,
  SharedGrant,
  SharedPackageSummary,
  SharedReadRecord,
  SharedReadResult,
} from '@/data/shared-package'

async function parse<T>(response: Response, fallback: string): Promise<T> {
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = payload?.detail
    const message =
      (detail && typeof detail === 'object' ? detail.message : detail) || payload?.message || fallback
    throw new Error(typeof message === 'string' ? message : fallback)
  }
  return payload as T
}

export async function listOwnerPackages(ownerDept: string): Promise<SharedPackageSummary[]> {
  const query = new URLSearchParams({ actor_dept: ownerDept }).toString()
  const response = await request(`/api/shared-packages?${query}`)
  const payload = await parse<{ items: SharedPackageSummary[] }>(response, '共享包列表读取失败')
  return payload.items
}

export async function loadOwnerPackage(
  packageId: number,
  ownerDept: string,
): Promise<SharedPackageSummary> {
  const query = new URLSearchParams({ actor_dept: ownerDept }).toString()
  const response = await request(`/api/shared-packages/${packageId}?${query}`)
  return parse<SharedPackageSummary>(response, '共享包详情读取失败')
}

export async function listAvailable(recipientDept: string): Promise<AvailablePackage[]> {
  const query = new URLSearchParams({ recipient_dept: recipientDept }).toString()
  const response = await request(`/api/shared-packages/available?${query}`)
  const payload = await parse<{ items: AvailablePackage[] }>(response, '可用共享包读取失败')
  return payload.items
}

export async function openSharedPackage(
  packageId: number,
  credentialToken: string,
  options: { regions?: string[]; dimensions?: string[]; holdMs?: number } = {},
): Promise<SharedReadResult> {
  const response = await request(`/api/shared-packages/${packageId}/open`, {
    method: 'POST',
    body: JSON.stringify({
      credential_token: credentialToken,
      regions: options.regions,
      dimensions: options.dimensions,
      hold_ms: options.holdMs ?? 0,
    }),
  })
  return parse<SharedReadResult>(response, '共享包读取失败')
}

export async function drillSharedPackage(
  packageId: number,
  credentialToken: string,
  options: { regions?: string[]; dimensions?: string[]; holdMs?: number } = {},
): Promise<SharedReadResult> {
  const response = await request(`/api/shared-packages/${packageId}/drill`, {
    method: 'POST',
    body: JSON.stringify({
      credential_token: credentialToken,
      regions: options.regions,
      dimensions: options.dimensions,
      hold_ms: options.holdMs ?? 0,
    }),
  })
  return parse<SharedReadResult>(response, '共享包下钻失败')
}

export async function executeDrillRule(
  ruleId: number,
  credentialToken: string,
  options: { regions?: string[]; dimensions?: string[]; holdMs?: number } = {},
): Promise<SharedReadResult> {
  const response = await request(`/api/drill-path/${ruleId}/execute`, {
    method: 'POST',
    body: JSON.stringify({
      credential_token: credentialToken,
      regions: options.regions,
      dimensions: options.dimensions,
      hold_ms: options.holdMs ?? 0,
    }),
  })
  return parse<SharedReadResult>(response, '下钻执行失败')
}

export async function issueGrant(
  packageId: number,
  ownerDept: string,
  values: { recipient_dept: string; regions: string[]; dimensions?: string[]; grant_version?: number },
): Promise<{ grant: SharedGrant; credential: SharedCredential }> {
  const query = new URLSearchParams({ owner_dept: ownerDept }).toString()
  const response = await request(`/api/shared-packages/${packageId}/grants?${query}`, {
    method: 'POST',
    body: JSON.stringify(values),
  })
  return parse(response, '授权签发失败')
}

export async function revokeGrant(
  packageId: number,
  grantId: number,
  ownerDept: string,
): Promise<{ grant: SharedGrant; credential: SharedCredential | null }> {
  const query = new URLSearchParams({ owner_dept: ownerDept }).toString()
  const response = await request(
    `/api/shared-packages/${packageId}/grants/${grantId}/revoke?${query}`,
    { method: 'POST' },
  )
  return parse(response, '授权撤销失败')
}

export async function listReads(
  packageId: number,
  ownerDept: string,
): Promise<{ reads: SharedReadRecord[]; total: number; provenance: Record<string, string> }> {
  const query = new URLSearchParams({ owner_dept: ownerDept }).toString()
  const response = await request(`/api/shared-packages/${packageId}/reads?${query}`)
  return parse(response, '读记录读取失败')
}

export async function getRead(readId: string): Promise<SharedReadRecord & Record<string, unknown>> {
  const response = await request(`/api/shared-packages/reads/${encodeURIComponent(readId)}`)
  return parse(response, '读记录不存在')
}

export async function dashboardPackageData(
  dashboardId: number,
  recipientDept: string,
): Promise<Record<string, unknown>> {
  const query = new URLSearchParams({ recipient_dept: recipientDept }).toString()
  const response = await request(`/api/dashboard-builder/${dashboardId}/data?${query}`)
  return parse(response, '仪表盘共享数据读取失败')
}
