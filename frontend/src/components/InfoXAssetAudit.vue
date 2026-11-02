<script setup lang="ts">
import { computed } from 'vue'
import type { InfoXAssetAudit } from '../types'

const props = defineProps<{ audit: InfoXAssetAudit }>()
const counts = computed(() => {
  const items = props.audit.items
  return ['detected', 'not_detected', 'unknown', 'favorite_exempt'].map(key => items.filter(item => item.verdict === key).length)
})
const labels: Record<string, string> = {
  detected: '기업명 검출 · 대체 검토 대상', not_detected: '검사한 메타데이터에서 미검출',
  unknown: '확인 불가', favorite_exempt: '즐겨찾기 예외',
  metadata_missing: '해당 파일의 검사할 메타데이터가 없습니다.',
  reference_incomplete: '배정 파일·구간을 정확히 식별하지 못했습니다.',
  ambiguous_media: '영상과 사진 참조가 함께 있어 실제 배정 대상을 확정하지 못했습니다.',
  metadata_unreadable: '일부 메타데이터의 구조를 읽지 못해 검사를 완료하지 못했습니다.',
  verified_favorite_replacement: '검증된 즐겨찾기 교체 이력과 일치합니다.',
  authentication_required: 'InfoX 백엔드 인증을 확인한 뒤 다시 검사하세요.',
  read_failed: '현재 영상 데이터를 읽거나 검사하지 못했습니다. 다시 검사할 수 있습니다.',
  draft_changed: '서버 대본·음성·자막이 확정 전송본과 달라 에셋 검사를 보류했습니다.',
  inspection_incomplete: '확인 불가 항목이나 검사하지 못한 구조가 있습니다.',
  unsupported_template: '이 템플릿의 자동 배정 구조는 아직 검증되지 않았습니다.',
  invalid_video: '조회한 영상이 현재 실행과 다릅니다.', invalid_scenes: '장면 구조를 읽지 못했습니다.',
  scene_id_missing: '장면 식별자가 없습니다.', invalid_graph: '레이어 연결 구조를 읽지 못했습니다.',
  missing_layer: '연결된 레이어가 없습니다.', cycle_or_shared_layer: '레이어 연결이 순환하거나 중복됩니다.',
  invalid_marker: '자동 배정 여부를 확인하지 못했습니다.', unsupported_layer: '지원하지 않는 미디어 레이어입니다.',
  invalid_media: '배정 미디어 구조를 읽지 못했습니다.', invalid_bundle: '사진 묶음 구조를 읽지 못했습니다.',
  inspection_limit: '검사 범위를 초과해 일부 정보를 확인하지 못했습니다.',
  no_inspectable_assets: '검사할 자동 배정 에셋을 확인하지 못했습니다.', version_missing: '서버 상태의 버전을 확인하지 못했습니다.',
}
</script>

<template>
  <section v-if="audit.state !== 'not_started'" class="infox-asset-audit" aria-label="기업 에셋 검사 결과">
    <h4>기업 에셋 검사</h4>
    <p v-if="audit.state === 'running'">배정 에셋을 검사하는 중…</p>
    <template v-else>
      <p>기업명 검출 {{ counts[0] }} · 메타데이터 미검출 {{ counts[1] }} · 확인 불가 {{ counts[2] }} · 즐겨찾기 예외 {{ counts[3] }}</p>
      <p v-if="audit.error_code">{{ labels[audit.error_code] ?? '검사 상태를 확인하세요.' }}</p>
      <p>메타데이터 검사 결과입니다. 영상 속 기업명·로고의 시각적 노출 부재를 보증하지 않습니다. 교체·저장은 아직 수행하지 않았습니다.</p>
      <ul v-if="audit.issues.length">
        <li v-for="(issue, index) in audit.issues" :key="index">
          <template v-if="issue.scene_index != null">장면 {{ issue.scene_index + 1 }} · </template>{{ labels[issue.code] ?? issue.code }}
        </li>
      </ul>
      <ul class="infox-asset-items">
        <li v-for="(item, index) in audit.items" :key="index" :data-verdict="item.verdict">
          <strong>장면 {{ item.scene_index + 1 }}<template v-if="item.item_index != null"> · 사진 {{ item.item_index + 1 }}</template> · {{ labels[item.verdict] }}</strong>
          <p v-for="(hit, hitIndex) in item.hits" :key="hitIndex">{{ hit.term }} · {{ hit.field }}</p>
          <p v-if="labels[item.reason]">{{ labels[item.reason] }}</p>
          <details>
            <summary>배정 파일과 검사 근거</summary>
            <p>레이어 {{ item.layer_id }} · {{ item.reference.bucket ?? '버킷 미확인' }} / {{ item.reference.filename ?? '파일 미확인' }}</p>
            <p v-if="item.reference.segment">구간 {{ item.reference.segment }}</p>
            <p v-for="source in item.metadata_sources" :key="source">{{ source }}</p>
          </details>
        </li>
      </ul>
    </template>
  </section>
</template>
