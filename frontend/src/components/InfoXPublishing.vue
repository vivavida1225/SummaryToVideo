<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import InfoXDownloads from './InfoXDownloads.vue'
import { api } from '../api'
import { editorEligible } from '../infoxEditor'
import { PublishingControl, formatPublishMetadata, publishStages, publishErrors } from '../infoxPublishing'
import type { InfoXRun } from '../types'
const props=defineProps<{run:InfoXRun; initialMarketDate?:string}>()
const control=new PublishingControl(api,props.initialMarketDate)
const {record,history,preparing,marketDate,busy,loading,uncertain,error,charge,canStart,canPrepare}=control
const drafting=computed(()=>!record.value || preparing.value)
const metadata=computed(()=>drafting.value ? formatPublishMetadata(marketDate.value) : record.value?.snapshot)
const older=computed(()=>history.value.filter(r=>r.id!==record.value?.id))
watch(()=>props.run.id,id=>{void control.restore(id,editorEligible(props.run))},{immediate:true})
watch(()=>editorEligible(props.run),eligible=>{void control.setEligibility(eligible)})
onBeforeUnmount(()=>control.dispose())
</script>

<template>
  <section class="infox-asset-audit" aria-label="렌더 및 유튜브 업로드">
    <h4>렌더 및 유튜브 업로드</h4>
    <p>InfoX에서 편집 내용을 저장한 뒤 실행하세요. 실행 중에는 편집을 계속하지 마세요.</p>
    <p>대상 영상: {{ record?.video_id ?? run.video_id }}</p>
    <p v-if="record">현재 게시 회차: {{ record.attempt_number ?? 1 }}</p>
    <button v-if="record && ['completed','failed','needs_attention','clipboard_failed'].includes(record.stage) && !preparing" type="button" class="ghost-button" :disabled="!canPrepare || !editorEligible(run)" @click="control.prepareNew">새 회차 렌더·업로드 준비</button>
    <template v-if="preparing">
      <p>새 회차를 준비합니다. 최신 저장본을 렌더하고 새 YouTube 링크를 만듭니다. 기존 영상과 링크는 유지됩니다.</p>
      <button type="button" class="ghost-button" :disabled="busy" @click="control.cancelPreparation">준비 취소</button>
    </template>
    <label :for="'publish-date-'+run.id">게시 기준일</label>
    <input :id="'publish-date-'+run.id" v-model="marketDate" type="date" :disabled="!drafting || busy || loading || uncertain" />
    <template v-if="metadata">
      <p>{{ metadata.title }}</p>
      <p>{{ metadata.description }}</p>
    </template>
    <p v-else>올바른 게시 기준일을 선택하세요.</p>
    <p>공개 범위: 일부공개 (unlisted)</p>
    <p v-if="drafting">렌더 이용량: {{ charge?.available ? charge.charged === true ? '과금 대상' : charge.charged === false ? '과금 없음' : '조회 완료 · 상세 이용량 확인 불가' : '확인 중 또는 조회 불가' }}</p>
    <p v-if="drafting && charge?.credits != null">예상 차감 크레딧: {{ charge.credits }}<template v-if="charge.duration_seconds != null"> · 영상 길이 {{ charge.duration_seconds }}초</template></p>
    <p v-if="drafting && charge?.blocked_reason" role="alert">{{ publishErrors[charge.blocked_reason] ?? '게시 조건을 확인하지 못했습니다. 다시 준비하세요.' }}</p>
    <button type="button" class="ghost-button" :disabled="!canStart || !editorEligible(run)" @click="control.start">편집 완료 · 렌더 및 유튜브 업로드</button>
    <p v-if="!editorEligible(run)">영상 저장·검증 완료 후 게시를 시작할 수 있습니다.</p>
    <div v-if="record" aria-live="polite">
      <p role="status">{{ publishStages[record.stage] ?? '게시 상태 확인 필요' }}</p>
      <template v-if="['rendering','generating_metadata','uploading'].includes(record.stage)">
        <p v-if="record.progress == null">진행 중 · 진행률 확인 중</p>
        <template v-else>
          <progress :value="record.progress" max="100" aria-label="게시 단계 진행률" />
          <p>{{ record.progress }}% · 작업별 완료 결과를 확인합니다.</p>
        </template>
      </template>
      <p v-if="record.error_code">{{ publishErrors[record.error_code] ?? '기존 게시 기록을 확인하세요.' }}</p>
      <p v-if="record.failed_stage">중단 단계: {{ publishStages[record.failed_stage] ?? '확인 필요' }}</p>
      <p v-if="record.uploaded_verified">InfoX의 해당 업로드 작업 결과와 일부공개 기록을 확인했습니다.</p>
      <a v-if="record.uploaded_verified && record.share_url" :href="record.share_url" target="_blank" rel="noopener noreferrer">{{ record.share_url }}</a>
      <button v-if="record.stage==='clipboard_failed' && !preparing" type="button" class="ghost-button" :disabled="busy || loading" @click="control.copy">링크 다시 복사</button>
      <InfoXDownloads :key="record.id" :publication-id="record.id" :uploaded-verified="record.uploaded_verified" />
    </div>
    <details v-if="older.length">
      <summary>이전 게시 회차 ({{ older.length }})</summary>
      <ul>
        <li v-for="item in older" :key="item.id">
          <p>{{ item.attempt_number }}회차 · {{ item.snapshot.market_date }} · {{ publishStages[item.stage] ?? '상태 확인 필요' }}</p>
          <p v-if="item.created_at"><time :datetime="item.created_at">{{ new Date(item.created_at).toLocaleString('ko-KR', {timeZone:'Asia/Seoul'}) }}</time></p>
          <a v-if="item.uploaded_verified && item.share_url" :href="item.share_url" target="_blank" rel="noopener noreferrer">{{ item.share_url }}</a>
          <InfoXDownloads :key="item.id" :publication-id="item.id" :uploaded-verified="item.uploaded_verified" />
        </li>
      </ul>
    </details>
    <p v-if="error" role="alert">{{ error }}</p>
  </section>
</template>
