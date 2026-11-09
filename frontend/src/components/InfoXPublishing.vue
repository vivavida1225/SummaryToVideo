<script setup lang="ts">
import { computed } from 'vue'
import InfoXDownloads from './InfoXDownloads.vue'
import { editorEligible } from '../infoxEditor'
import { PublishingControl, formatPublishMetadata, publishStages, publishErrors } from '../infoxPublishing'
import type { InfoXRun } from '../types'

// The controller is owned by the parent so collapsing a step or moving between
// steps never disposes it or re-runs its requests. Start/copy live in the
// parent's action area; this component shows settings, status and history.
const props = defineProps<{ run: InfoXRun; control: PublishingControl }>()
const { record, history, preparing, marketDate, busy, loading, uncertain, error, charge } = props.control
const drafting = computed(() => !record.value || preparing.value)
const metadata = computed(() => drafting.value ? formatPublishMetadata(marketDate.value) : record.value?.snapshot)
const older = computed(() => history.value.filter(r => r.id !== record.value?.id))
const chargeLabel = computed(() => !charge.value?.available ? '확인 중 또는 조회 불가'
  : charge.value.charged === true ? '과금 대상' : charge.value.charged === false ? '과금 없음' : '조회 완료 · 상세 이용량 확인 불가')
</script>

<template>
  <section class="infox-publishing" aria-label="렌더 및 유튜브 업로드">
    <p v-if="drafting" class="infox-guide">InfoX 편집기에서 편집 내용을 저장한 뒤 실행하세요. 실행 중에는 편집을 계속하지 마세요.</p>
    <p v-if="!editorEligible(run)">영상 저장·검증 완료 후 게시를 시작할 수 있습니다.</p>
    <template v-if="preparing">
      <p>새 회차를 준비합니다. 최신 저장본을 렌더하고 새 YouTube 링크를 만듭니다. 기존 영상과 링크는 유지됩니다.</p>
      <button type="button" class="ghost-button" :disabled="busy" @click="control.cancelPreparation()">준비 취소</button>
    </template>
    <dl class="infox-facts">
      <div>
        <dt><label :for="'publish-date-' + run.id">게시 기준일</label></dt>
        <dd><input :id="'publish-date-' + run.id" v-model="marketDate" type="date" :disabled="!drafting || busy || loading || uncertain" /></dd>
      </div>
      <template v-if="metadata">
        <div><dt>제목</dt><dd>{{ metadata.title }}</dd></div>
        <div><dt>설명</dt><dd>{{ metadata.description }}</dd></div>
      </template>
      <div v-else><dt>제목·설명</dt><dd>올바른 게시 기준일을 선택하세요.</dd></div>
      <div><dt>공개 범위</dt><dd>일부공개 (unlisted)</dd></div>
      <div v-if="drafting">
        <dt>렌더 이용량</dt>
        <dd>{{ chargeLabel }}<template v-if="charge?.credits != null"> · 예상 차감 크레딧 {{ charge.credits }}</template><template v-if="charge?.credits != null && charge.duration_seconds != null"> · 영상 길이 {{ charge.duration_seconds }}초</template></dd>
      </div>
    </dl>
    <p v-if="drafting && charge?.blocked_reason" class="inline-error" role="alert">{{ publishErrors[charge.blocked_reason] ?? '게시 조건을 확인하지 못했습니다. 다시 준비하세요.' }}</p>
    <div v-if="record" class="infox-publish-status" aria-live="polite">
      <p role="status">{{ publishStages[record.stage] ?? '게시 상태 확인 필요' }}</p>
      <p v-if="record.error_code" :class="{ 'inline-error': ['failed', 'needs_attention'].includes(record.stage) }">{{ publishErrors[record.error_code] ?? '기존 게시 기록을 확인하세요.' }}</p>
      <p v-if="record.failed_stage">중단 단계: {{ publishStages[record.failed_stage] ?? '확인 필요' }}</p>
    </div>
    <details class="infox-disclosure">
      <summary>게시 기록과 식별 정보</summary>
      <p>대상 영상: {{ record?.video_id ?? run.video_id }}</p>
      <p v-if="record">현재 게시 회차: {{ record.attempt_number ?? 1 }}</p>
      <p v-if="!older.length">이전 게시 회차가 없습니다.</p>
    </details>
    <details v-if="older.length" class="infox-disclosure">
      <summary>이전 게시 회차 ({{ older.length }})</summary>
      <ul>
        <li v-for="item in older" :key="item.id">
          <p>{{ item.attempt_number }}회차 · {{ item.snapshot.market_date }} · {{ publishStages[item.stage] ?? '상태 확인 필요' }}</p>
          <p v-if="item.created_at"><time :datetime="item.created_at">{{ new Date(item.created_at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) }}</time></p>
          <a v-if="item.uploaded_verified && item.share_url" :href="item.share_url" target="_blank" rel="noopener noreferrer">{{ item.share_url }}</a>
          <InfoXDownloads :key="item.id" :publication-id="item.id" :uploaded-verified="item.uploaded_verified" />
        </li>
      </ul>
    </details>
    <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
  </section>
</template>
