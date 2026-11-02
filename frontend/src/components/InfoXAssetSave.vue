<script setup lang="ts">
import type { InfoXAssetSave } from '../types'
defineProps<{ save: InfoXAssetSave }>()
const labels: Record<string, string> = {
  checking_source: '최신 서버 상태와 고정한 교체 대상을 확인하는 중…',
  saving: '편집 데이터를 저장하는 중…',
  verifying: '서버를 다시 읽어 적용·보존 결과를 확인하는 중…',
  saved_verified: '서버 저장·재조회 검증 완료',
  unchanged_verified: '무변경 재조회 검증 완료 · 서버 저장 없음',
  needs_attention: '저장·적용 검증에 확인이 필요합니다.',
}
const errors: Record<string, string> = {
  source_changed: '서버 버전이나 배치가 계획 이후 달라졌습니다. 기존 데이터는 덮어쓰지 않았습니다.',
  draft_changed: '대본·음성·자막이 확정본과 달라 저장을 보류했습니다.',
  selection_unavailable: '고정한 즐겨찾기 구간을 확인하지 못했습니다. 다른 후보로 변경하지 않습니다.',
  context_invalid: '서버 편집 컨텍스트 또는 저장 버전을 확인하지 못했습니다.',
  transform_invalid: '재구성한 제안본의 참조·보존 검증이 실패했습니다.',
  version_conflict: '다른 편집의 서버 버전과 충돌했습니다. 현재 적용 상태부터 확인합니다.',
  save_rejected: '저장 요청이 거절되었습니다. 현재 서버 상태를 확인하세요.',
  save_unconfirmed: '저장 적용을 확인하지 못했습니다. 같은 영상의 서버 상태부터 확인해야 합니다.',
  verification_failed: '저장 응답 뒤 서버의 적용·보존 결과가 제안본과 일치하지 않습니다.',
  authentication_required: 'InfoX 백엔드 인증 설정을 확인하세요.',
  read_failed: '서버 조회를 완료하지 못했습니다. 보관한 저장 기록을 유지합니다.',
}
</script>

<template>
  <section v-if="save.state !== 'not_started'" class="infox-asset-audit" aria-label="저장 및 적용 검증">
    <h4>저장 및 적용 검증</h4>
    <p>{{ labels[save.state] ?? '저장 기록을 확인하세요.' }}</p>
    <p v-if="save.error_code">{{ errors[save.error_code] ?? '서버 적용 상태를 확인하세요.' }}</p>
    <p v-if="save.write_error_code">최초 저장 요청 결과: {{ errors[save.write_error_code] }}<template v-if="save.write_status"> (HTTP {{ save.write_status }})</template></p>
    <p v-if="save.read_error_code">후속 서버 조회 결과: {{ errors[save.read_error_code] }}</p>
    <p v-if="['intent_recorded', 'unknown'].includes(save.receipt_state) && save.state !== 'saved_verified'">저장을 재전송하지 않고 기존 영상의 적용 여부를 조회합니다.</p>
    <p v-if="save.state === 'saved_verified'">고정한 구간과 보존 항목을 서버에서 확인했습니다. 실제 편집기의 재생 확인은 별도입니다. 수동 링크에서 반복·잘림·소재 원음·TTS·자막을 확인하세요.</p>
    <p v-if="save.verified_version">검증한 서버 버전: {{ save.verified_version }}</p>
  </section>
</template>
