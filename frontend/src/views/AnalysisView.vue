<script setup lang="ts">
import { reactive } from 'vue'
import { useWorkspaceContext } from '../composables/workspaceContext'
const ctx = reactive(useWorkspaceContext())
</script>
<template>
  <section>
    <header class="page-head">
      <div>
        <h1>分析</h1>
        <p>チャンネル統計と録画量の推移</p>
      </div>
    </header>
    <div class="toolbar">
      <label class="field"
        >チャンネル<select v-model="ctx.analysisChannel" @change="ctx.refreshAnalysis">
          <option v-for="channel in ctx.channels" :key="channel.id" :value="channel.id">
            {{ channel.name }}
          </option>
        </select>
      </label>
      <label class="field"
        >期間<select v-model="ctx.analysisPeriod" @change="ctx.refreshAnalysis">
          <option value="30">30 日</option>
          <option value="90">90 日</option>
          <option value="180">180 日</option>
          <option value="365">365 日</option>
        </select>
      </label>
    </div>
    <div v-if="ctx.analysisData" class="grid stats-grid">
      <div class="card">
        <small>録画件数</small>
        <strong>{{ ctx.analysisRecordings.count }}</strong>
      </div>
      <div class="card">
        <small>録画容量</small>
        <strong>{{ ctx.formatBytes(ctx.analysisRecordings.total_bytes) }}</strong>
      </div>
      <div class="card">
        <small>保護済み</small>
        <strong>{{ ctx.analysisRecordings.protected_count }}</strong>
      </div>
    </div>
    <div class="card">
      <h2>チャンネル推移</h2>
      <div v-if="ctx.analysisPoints.length > 0" class="chart">
        <svg viewBox="0 0 700 220" role="img" aria-label="チャンネル統計推移">
          <line x1="5" y1="15" x2="695" y2="15" stroke="var(--line)" />
          <line x1="5" y1="205" x2="695" y2="205" stroke="var(--line)" />
          <text x="5" y="12" class="chart-axis">{{ ctx.chartMaximum.toLocaleString() }}</text>
          <text x="5" y="218" class="chart-axis">{{ ctx.chartMinimum.toLocaleString() }}</text>
          <path
            v-for="(segment, index) in ctx.chartSegments"
            :key="index"
            :d="segment"
            fill="none"
            stroke="var(--accent)"
            stroke-width="3"
          />
          <template v-for="(point, index) in ctx.analysisPoints" :key="point.captured_at">
            <circle
              v-if="point.subscriber_count !== null"
              :cx="ctx.chartX(index)"
              :cy="ctx.chartY(point.subscriber_count)"
              r="4"
              fill="var(--accent)"
            >
              <title>
                {{ ctx.formatDate(point.captured_at) }} · {{ point.subscriber_count.toLocaleString() }} 人
              </title>
            </circle>
          </template>
        </svg>
        <div class="meta">
          <span>{{ ctx.formatDate(String(ctx.analysisPoints[0]?.captured_at || '')) }}</span>
          <span>{{
            ctx.formatDate(String(ctx.analysisPoints[ctx.analysisPoints.length - 1]?.captured_at || ''))
          }}</span>
        </div>
      </div>
      <div v-else class="empty-state">
        この期間の統計データはありません。チャンネルの監視を有効にすると、定期確認時に統計が記録されます。
      </div>
    </div>
  </section>
</template>
