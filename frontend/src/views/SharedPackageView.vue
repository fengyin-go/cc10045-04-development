<template>
  <section class="page shared-page" data-testid="shared-page">
    <header class="page-head">
      <div>
        <h2>共享包核对</h2>
        <p class="page-desc">
          只读共享包：拥有方按区域和维度授权，接收方只能看到被分享范围内的聚合明细，
          每次打开/下钻都留一条请求时与返回时授权状态可查的读记录。
        </p>
      </div>
      <div class="page-actions">
        <label class="inline-field">
          <span>我的部门</span>
          <select v-model="actorDept" data-testid="actor-dept" @change="onDeptChange">
            <option v-for="dept in departments" :key="dept" :value="dept">{{ dept }}</option>
          </select>
        </label>
        <button class="btn" data-testid="refresh" type="button" :disabled="busy" @click="refreshAll">
          刷新
        </button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="error">{{ errorMessage }}</p>

    <div class="shared-grid">
      <!-- ============ 拥有方 ============ -->
      <section class="shared-panel" data-testid="owner-panel">
        <h3>拥有方视图 · {{ actorDept }}</h3>
        <template v-if="ownerPackages.length">
          <table class="data-table compact">
            <thead>
              <tr><th>共享包</th><th>包内样本</th><th>区域分布</th><th>数据版本</th><th>状态</th></tr>
            </thead>
            <tbody>
              <tr
                v-for="pkg in ownerPackages"
                :key="pkg.id"
                :class="{ selected: pkg.id === selectedPackageId }"
                :data-testid="`owner-package-${pkg.id}`"
                @click="selectPackage(pkg.id)"
              >
                <td>{{ pkg.package_name }}</td>
                <td>{{ pkg.sample_count }} 条</td>
                <td>
                  <span v-for="(count, region) in pkg.region_counts" :key="region" class="chip">
                    {{ region }} {{ count }}
                  </span>
                </td>
                <td>v{{ pkg.data_version }}</td>
                <td>{{ pkg.status }}</td>
              </tr>
            </tbody>
          </table>

          <template v-if="ownerDetail">
            <h4>授权管理</h4>
            <div class="analysis-form">
              <label>
                <span>接收部门</span>
                <input v-model="grantForm.recipient" data-testid="grant-recipient" placeholder="如 运营分析部" />
              </label>
              <label>
                <span>授权区域（逗号分隔）</span>
                <input v-model="grantForm.regions" data-testid="grant-regions" :placeholder="ownerDetail.data_scope.join(',')" />
              </label>
              <label>
                <span>授权维度</span>
                <input v-model="grantForm.dimensions" data-testid="grant-dimensions" :placeholder="ownerDetail.dimensions.join(',')" />
              </label>
              <button class="btn primary" data-testid="issue-grant" type="button" :disabled="busy" @click="submitGrant">
                签发只读授权
              </button>
            </div>
            <p v-if="grantMessage" class="hint" data-testid="grant-message">{{ grantMessage }}</p>

            <table class="data-table compact" data-testid="grant-table">
              <thead>
                <tr><th>接收部门</th><th>授权区域</th><th>版本</th><th>状态</th><th>操作</th></tr>
              </thead>
              <tbody>
                <tr v-for="grant in ownerDetail.grants" :key="grant.id" :data-testid="`grant-${grant.id}`">
                  <td>{{ grant.recipient_dept }}</td>
                  <td>{{ grant.regions.join('、') }}</td>
                  <td>v{{ grant.grant_version }}</td>
                  <td :data-status="grant.status">{{ grant.status }}</td>
                  <td>
                    <button
                      v-if="grant.status === '已授权'"
                      class="link danger"
                      data-testid="revoke-grant"
                      type="button"
                      :disabled="busy"
                      @click="submitRevoke(grant.id)"
                    >
                      撤销授权
                    </button>
                    <span v-else class="muted">{{ grant.revoked_at || '—' }}</span>
                  </td>
                </tr>
                <tr v-if="!ownerDetail.grants?.length">
                  <td colspan="5" class="empty-state">尚未签发任何授权</td>
                </tr>
              </tbody>
            </table>

            <h4>读记录（追查来源与当时版本）</h4>
            <table class="data-table compact" data-testid="read-table">
              <thead>
                <tr>
                  <th>读记录</th><th>操作</th><th>接收部门</th>
                  <th>请求时</th><th>返回时</th><th>结果</th><th>交付</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="read in reads"
                  :key="read.read_id"
                  :data-testid="`read-${read.read_id}`"
                  @click="inspectRead(read.read_id)"
                >
                  <td class="mono">{{ read.read_id }}</td>
                  <td>{{ read.operation }}</td>
                  <td>{{ read.recipient_dept || '—' }}</td>
                  <td>
                    <span :data-auth="read.request_auth_status">{{ read.request_auth_status }}</span>
                    <small class="muted">v{{ read.request_grant_version ?? '—' }}</small>
                  </td>
                  <td>
                    <span :data-auth="read.return_auth_status">{{ read.return_auth_status }}</span>
                    <small class="muted">v{{ read.return_grant_version ?? '—' }}</small>
                  </td>
                  <td :data-outcome="read.outcome">{{ read.outcome }}</td>
                  <td>
                    {{ read.delivered_count }} 条
                    <span v-for="region in read.delivered_regions" :key="region" class="chip">{{ region }}</span>
                  </td>
                </tr>
                <tr v-if="!reads.length">
                  <td colspan="7" class="empty-state">还没有读记录</td>
                </tr>
              </tbody>
            </table>

            <div v-if="readTrace" class="read-trace" data-testid="read-trace">
              <h4>读记录核对 · {{ readTrace.read_id }}</h4>
              <div class="trace-columns">
                <div class="trace-card">
                  <strong>请求时</strong>
                  <p>{{ readTrace.request_auth_status }} · 授权 v{{ readTrace.request_grant_version ?? '—' }}</p>
                  <p>包版本 v{{ readTrace.request_package_version ?? '—' }}</p>
                  <p>范围：{{ readTrace.request_scope.join('、') || '—' }}</p>
                  <p class="muted">{{ readTrace.request_time }}</p>
                </div>
                <div class="trace-card">
                  <strong>返回时</strong>
                  <p>{{ readTrace.return_auth_status }} · 授权 v{{ readTrace.return_grant_version ?? '—' }}</p>
                  <p>包版本 v{{ readTrace.return_package_version ?? '—' }}</p>
                  <p>范围：{{ readTrace.return_scope.join('、') || '—' }}</p>
                  <p class="muted">{{ readTrace.return_time || '—' }}</p>
                </div>
                <div class="trace-card">
                  <strong>交付核对</strong>
                  <p>{{ readTrace.outcome }} · {{ readTrace.delivered_count }} 条</p>
                  <p>区域：{{ readTrace.delivered_regions.join('、') || '—' }}</p>
                  <p class="mono hash">{{ shortHash(readTrace.delivered_hash) }}</p>
                  <p v-if="readTrace.verification">
                    版本核对：{{ readTrace.verification.version_matches ? '一致' : '已变化' }}
                  </p>
                </div>
              </div>
              <table v-if="readTrace.delivered_rows.length" class="data-table compact">
                <thead>
                  <tr><th>样本</th><th>区域</th><th>维度</th><th>事件</th><th>度量</th><th>发生时间</th></tr>
                </thead>
                <tbody>
                  <tr v-for="row in readTrace.delivered_rows" :key="row.id">
                    <td>{{ row.sample_key }}</td><td>{{ row.region }}</td>
                    <td>{{ row.dimension_key }}</td><td>{{ row.event_name }}</td>
                    <td>{{ row.measure }}</td><td>{{ row.occurred_at }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
        </template>
        <p v-else class="empty-state">该部门名下没有只读共享包</p>
      </section>

      <!-- ============ 接收方 ============ -->
      <section class="shared-panel" data-testid="recipient-panel">
        <h3>接收方视图 · {{ actorDept }}</h3>
        <p class="hint">
          这里只显示被授权的共享包与区域；未领取授权的部门看不到包，也取不到样本。
        </p>
        <template v-if="available.length">
          <div
            v-for="item in available"
            :key="`${item.package.id}-${item.credential.id}`"
            class="recipient-card"
            :data-testid="`recipient-package-${item.package.id}`"
          >
            <div class="card-head">
              <strong>{{ item.package.package_name }}</strong>
              <span class="chip">{{ item.package.owner_dept }}</span>
              <span class="chip">授权 v{{ item.credential.grant_version }}</span>
            </div>
            <p class="muted">
              可见范围：{{ item.scope.regions.join('、') }} · 维度 {{ item.scope.dimensions.join('、') }}
              · 共 {{ item.package.sample_count }} 条样本
            </p>
            <table class="data-table compact">
              <thead><tr><th>区域</th><th>维度</th><th>样本数</th><th>度量合计</th></tr></thead>
              <tbody>
                <tr v-for="agg in item.aggregates" :key="`${agg.region}-${agg.dimension_key}`">
                  <td>{{ agg.region }}</td><td>{{ agg.dimension_key }}</td>
                  <td>{{ agg.sample_count }}</td><td>{{ agg.measure_sum }}</td>
                </tr>
              </tbody>
            </table>
            <div class="page-actions">
              <button
                class="btn"
                data-testid="open-package"
                type="button"
                :disabled="busy"
                @click="openPackage(item)"
              >
                打开聚合明细
              </button>
              <button
                class="btn primary"
                data-testid="drill-package"
                type="button"
                :disabled="busy"
                @click="drillPackage(item)"
              >
                下钻事件明细
              </button>
              <button
                class="btn ghost"
                data-testid="drill-interleaved"
                type="button"
                :disabled="busy"
                @click="drillInterleaved(item)"
              >
                交错读取（返回前留撤销窗口）
              </button>
            </div>
            <p v-if="lastResult" class="hint" data-testid="read-result">
              {{ lastResult.outcome }}：{{ lastResult.delivered_count }} 条
              · 读记录 <span class="mono">{{ lastResult.read_id }}</span>
            </p>
          </div>
        </template>
        <p v-else class="empty-state" data-testid="no-package">
          当前部门没有可用的共享包凭据
        </p>

        <div v-if="lastResult" class="read-result" data-testid="result-detail">
          <h4>本次交付</h4>
          <div class="trace-columns">
            <div class="trace-card">
              <strong>请求时授权</strong>
              <p>{{ lastResult.auth.request.status }}</p>
              <p class="muted">{{ lastResult.auth.request.time }}</p>
            </div>
            <div class="trace-card">
              <strong>返回时授权</strong>
              <p>{{ lastResult.auth.return.status }}</p>
              <p class="muted">{{ lastResult.auth.return.time }}</p>
            </div>
            <div class="trace-card">
              <strong>交付范围</strong>
              <p>{{ lastResult.auth.return.scope.join('、') || '未交付' }}</p>
              <p class="mono hash">{{ shortHash(lastResult.verification?.delivered_hash || '') }}</p>
            </div>
          </div>
          <table v-if="deliveredRows.length" class="data-table compact" data-testid="delivered-table">
            <thead>
              <tr><th>样本</th><th>区域</th><th>维度</th><th>事件</th><th>度量</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in deliveredRows" :key="row.id" :data-region="row.region">
                <td>{{ row.sample_key }}</td><td>{{ row.region }}</td>
                <td>{{ row.dimension_key }}</td><td>{{ row.event_name }}</td>
                <td>{{ row.measure }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="empty-state">本次没有交付任何样本</p>
        </div>
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  drillSharedPackage,
  getRead,
  issueGrant,
  listAvailable,
  listOwnerPackages,
  listReads,
  loadOwnerPackage,
  openSharedPackage,
  revokeGrant,
} from '@/api/shared-package'
import type {
  AvailablePackage,
  SharedPackageSummary,
  SharedReadRecord,
  SharedReadResult,
} from '@/data/shared-package'

const departments = ['数据平台部', '运营分析部', '财务部', '市场部']
const actorDept = ref('数据平台部')
const busy = ref(false)
const errorMessage = ref('')

const ownerPackages = ref<SharedPackageSummary[]>([])
const selectedPackageId = ref<number | null>(null)
const ownerDetail = ref<(SharedPackageSummary & { grants?: import('@/data/shared-package').SharedGrant[] }) | null>(null)
const available = ref<AvailablePackage[]>([])
const reads = ref<SharedReadRecord[]>([])
const readTrace = ref<(SharedReadRecord & Record<string, unknown>) | null>(null)
const lastResult = ref<SharedReadResult | null>(null)
const grantMessage = ref('')

const grantForm = ref({ recipient: '运营分析部', regions: '', dimensions: '' })

const deliveredRows = computed(() => {
  if (!lastResult.value) return []
  return lastResult.value.details ?? lastResult.value.events ?? []
})

function shortHash(value: string): string {
  return value ? `${value.slice(0, 16)}…` : '无交付摘要'
}

async function refreshAll() {
  errorMessage.value = ''
  busy.value = true
  try {
    ownerPackages.value = await listOwnerPackages(actorDept.value)
    if (!ownerPackages.value.some((pkg) => pkg.id === selectedPackageId.value)) {
      selectedPackageId.value = ownerPackages.value[0]?.id ?? null
    }
    if (selectedPackageId.value) {
      await loadDetail(selectedPackageId.value)
    } else {
      ownerDetail.value = null
      reads.value = []
    }
    available.value = await listAvailable(actorDept.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取失败'
  } finally {
    busy.value = false
  }
}

async function loadDetail(packageId: number) {
  ownerDetail.value = await loadOwnerPackage(packageId, actorDept.value)
  const payload = await listReads(packageId, actorDept.value)
  reads.value = payload.reads
  readTrace.value = null
  grantForm.value.regions = ownerDetail.value.data_scope.join(',')
  grantForm.value.dimensions = ownerDetail.value.dimensions.join(',')
}

async function selectPackage(packageId: number) {
  selectedPackageId.value = packageId
  errorMessage.value = ''
  try {
    await loadDetail(packageId)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '共享包详情读取失败'
  }
}

function onDeptChange() {
  lastResult.value = null
  readTrace.value = null
  selectedPackageId.value = null
  void refreshAll()
}

async function submitGrant() {
  if (!ownerDetail.value) return
  errorMessage.value = ''
  grantMessage.value = ''
  busy.value = true
  try {
    const regions = grantForm.value.regions
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
    const dimensions = grantForm.value.dimensions
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
    const result = await issueGrant(ownerDetail.value.id, actorDept.value, {
      recipient_dept: grantForm.value.recipient.trim(),
      regions,
      dimensions,
      grant_version: (ownerDetail.value.grants?.length ?? 0) + 1,
    })
    grantMessage.value = `已向 ${result.grant.recipient_dept} 签发授权（v${result.grant.grant_version}）`
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '授权签发失败'
  } finally {
    busy.value = false
  }
}

async function submitRevoke(grantId: number) {
  if (!ownerDetail.value) return
  errorMessage.value = ''
  grantMessage.value = ''
  busy.value = true
  try {
    await revokeGrant(ownerDetail.value.id, grantId, actorDept.value)
    grantMessage.value = '授权已撤销，后续读取立即失效'
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '授权撤销失败'
  } finally {
    busy.value = false
  }
}

async function openPackage(item: AvailablePackage) {
  await runRead(() => openSharedPackage(item.package.id, item.credential.token ?? ''))
}

async function drillPackage(item: AvailablePackage) {
  await runRead(() => drillSharedPackage(item.package.id, item.credential.token ?? ''))
}

async function drillInterleaved(item: AvailablePackage) {
  // 留出 1.5 秒窗口，拥有方可以在数据返回前撤销授权。
  await runRead(() => drillSharedPackage(item.package.id, item.credential.token ?? '', { holdMs: 1500 }))
}

async function runRead(task: () => Promise<SharedReadResult>) {
  errorMessage.value = ''
  busy.value = true
  try {
    lastResult.value = await task()
  } catch (error) {
    // 后端把被拒的读取也返回成带 read_id 的结构，错误里带 message。
    errorMessage.value = error instanceof Error ? error.message : '读取失败'
    lastResult.value = null
  } finally {
    busy.value = false
  }
}

async function inspectRead(readId: string) {
  errorMessage.value = ''
  try {
    readTrace.value = await getRead(readId)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读记录读取失败'
  }
}

onMounted(refreshAll)
</script>
