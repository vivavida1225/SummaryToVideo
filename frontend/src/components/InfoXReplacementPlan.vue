<script setup lang="ts">
import { computed } from 'vue'
import type { InfoXReplacementPlan } from '../types'

const props = defineProps<{ plan: InfoXReplacementPlan; saveStarted?: boolean }>()
const uniqueSegments = computed(() => new Set(props.plan.selections.map(x => `${x.candidate.id}:${x.candidate.video_id}:${x.candidate.segment}`)).size)
const verified = computed(() => ['scope_preserved', 'normal_assets_preserved', 'layout_preserved',
  'timing_preserved', 'references_consistent', 'old_active_references_removed'].every(key => props.plan.checks?.[key] === true))
const errors: Record<string, string> = {
  audit_required: '기업 에셋 검사를 완료한 뒤 계획을 만들 수 있습니다.',
  source_changed: '서버 버전 또는 배정 에셋이 달라졌습니다. 다시 검사한 뒤 기존 선택을 확인하세요.',
  draft_changed: '서버 영상의 장면·음성·자막 준비 조건을 확인하지 못해 계획을 보류했습니다.',
  candidates_empty: '반도체 즐겨찾기 검색 결과가 없습니다.',
  no_usable_candidate: '기술적으로 사용할 수 있는 즐겨찾기 구간을 찾지 못했습니다.',
  pagination_invalid: '검색 페이지가 일관되지 않아 전체 결과를 확인하지 못했습니다.',
  pagination_limit: '설정한 최대 페이지 수를 넘어 후보 선택을 보류했습니다.',
  availability_limit: '설정한 파일 확인 횟수 안에서 사용 가능한 구간을 찾지 못했습니다.',
  selection_unavailable: '고정한 구간을 다시 확인하지 못했습니다. 현재 선택을 유지합니다.',
  unsupported_uservideo: '사용자 영상 구간의 교체 구조는 아직 검증되지 않았습니다.',
  unsupported_timing: '현재 장면의 프레임·시간 구조로 변환을 검증하지 못했습니다.',
  unsupported_media: '현재 미디어 구조로 변환을 검증하지 못했습니다.',
  transform_invalid: '로컬 변환의 참조·보존 검증이 통과하지 못했습니다.',
  authentication_required: 'InfoX 백엔드 인증 설정을 확인한 뒤 같은 계획을 다시 확인하세요.',
  read_failed: '영상 또는 즐겨찾기를 읽지 못했습니다. 같은 계획을 다시 확인할 수 있습니다.',
}
</script>

<template>
  <section v-if="plan.state !== 'not_started'" class="infox-asset-audit" aria-label="즐겨찾기 교체 계획">
    <h4>즐겨찾기 교체 계획</h4>
    <p v-if="['selecting', 'selected'].includes(plan.state)">후보 선택과 로컬 변환을 확인하는 중…</p>
    <p v-if="plan.state === 'ready' && verified && !saveStarted">교체 계획 검증 완료 · 서버에는 저장하지 않았습니다.</p>
    <p v-if="plan.state === 'not_needed'">기업명 검출 대상이 없어 교체가 필요하지 않습니다. 서버에는 저장하지 않았습니다.</p>
    <p v-if="plan.state === 'disabled'">이 전송본은 자동 대체가 꺼져 있어 후보 선택을 생략했습니다.</p>
    <p v-if="plan.error_code">{{ errors[plan.error_code] ?? '교체 계획을 확인하세요.' }}</p>
    <template v-if="plan.selections.length">
      <p>고정 구간 {{ uniqueSegments }}개 · 대체 대상 {{ plan.selections.length }}개</p>
      <p>반도체 즐겨찾기에서 선택한 구간을 유지합니다. 저장·적용 결과와 실제 재생 확인을 구분합니다.</p>
      <ul class="infox-asset-items">
        <li v-for="(choice, index) in plan.selections" :key="index">
          <strong>장면 {{ choice.target.scene_index + 1 }}<template v-if="choice.target.item_index != null"> · 사진 {{ choice.target.item_index + 1 }}</template> → {{ choice.candidate.title || '선택한 즐겨찾기 구간' }}</strong>
          <p>구간 {{ choice.candidate.duration }}초 · 원래 슬롯 {{ Number((choice.slot_frames / 30).toFixed(3)) }}초 · 소재 원음 끔</p>
          <p v-if="choice.slot_frames > Math.floor(choice.candidate.duration * 30)">슬롯 길이에 맞춘 반복 계획입니다. 실제 재생은 저장 단계에서 확인합니다.</p>
          <details><summary>고정한 구간 확인</summary>
            <p>구간 ID {{ choice.candidate.id }} · 원영상 {{ choice.candidate.video_id }}</p>
            <p>{{ choice.candidate.bucket }} / {{ choice.candidate.segment }}</p>
            <p>원영상 위치 {{ choice.candidate.timestamp }}초 · 구간 번호 {{ choice.candidate.frame_index }}</p>
          </details>
        </li>
      </ul>
    </template>
  </section>
</template>
