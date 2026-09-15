<script setup lang="ts">
import { computed } from 'vue'

import type { Job, JobState, ModelOption } from '../types'

const props = defineProps<{ job: Job; models: ModelOption[] }>()

const stateLabel: Record<JobState, string> = {
  queued: '작업을 준비하는 중',
  serializing: '원문을 정돈하는 중',
  copying_serialized: '직렬화 원문을 복사하는 중',
  requesting: 'Gemini 응답을 기다리는 중',
  retry_wait: '다음 요청을 준비하는 중',
  validating: '결과 형식을 확인하는 중',
  copying_compressed: '압축 결과를 복사하는 중',
  completed: '영상 원고가 완성되었습니다',
  failed: '작업을 완료하지 못했습니다',
}

const currentLabel = computed(() => stateLabel[props.job.state])
const actualModel = computed(() => props.models.find(model => model.id === props.job.model)?.label ?? props.job.model)

function formatEventTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(date)
}
</script>

<template>
  <section class="status-panel" aria-labelledby="progress-heading">
    <div class="status-head" role="status" aria-live="polite" aria-atomic="true">
      <div>
        <p class="eyebrow">LIVE PROGRESS</p>
        <h2 id="progress-heading">{{ currentLabel }}</h2>
      </div>
      <span class="state-badge" :class="`state-${job.state}`">
        <i aria-hidden="true" />
        {{ job.state === 'completed' ? '완료' : job.state === 'failed' ? '확인 필요' : '진행 중' }}
      </span>
    </div>

    <div class="job-meta" aria-label="작업 정보">
      <span v-if="actualModel">{{ job.state === 'completed' ? '응답 모델' : '호출 모델' }}: {{ actualModel }}</span>
      <span>시도 {{ job.attempt }} / {{ job.max_attempts }}</span>
      <span v-if="job.key_number">API 키 {{ job.key_number }}</span>
      <span>{{ job.elapsed_seconds.toFixed(1) }}초</span>
    </div>

    <ol class="timeline" aria-label="진행 기록">
      <li v-for="(event, index) in job.events" :key="`${event.at}-${index}`">
        <span class="timeline-dot" aria-hidden="true" />
        <div>
          <p>{{ event.message }}</p>
          <time :datetime="event.at">{{ formatEventTime(event.at) }}</time>
        </div>
      </li>
      <li v-if="job.events.length === 0">
        <span class="timeline-dot" aria-hidden="true" />
        <div><p>{{ currentLabel }}</p></div>
      </li>
    </ol>
  </section>
</template>
