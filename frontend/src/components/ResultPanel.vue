<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  title: string
  eyebrow: string
  value: string
  label: string
  copyLabel: string
  downloadLabel: string
  busy?: boolean
  bodyCharCount?: number
  excessCharCount?: number
}>()

const charCount = computed(() => props.bodyCharCount ?? Array.from(props.value.replace(/[\r\n]/g, '')).length)

defineEmits<{ copy: []; download: [] }>()
</script>

<template>
  <article class="result-panel">
    <header>
      <div>
        <p class="eyebrow">{{ eyebrow }}</p>
        <h3>{{ title }}</h3>
      </div>
      <span class="char-count" title="개행 제외">{{ charCount.toLocaleString('ko-KR') }}자<template v-if="excessCharCount"> · 상한 {{ (charCount - excessCharCount).toLocaleString('ko-KR') }}자보다 {{ excessCharCount.toLocaleString('ko-KR') }}자 초과</template></span>
    </header>
    <label class="sr-only">{{ label }}</label>
    <textarea :aria-label="label" :value="value" readonly spellcheck="false" />
    <footer>
      <button class="ghost-button" type="button" :disabled="busy" @click="$emit('copy')">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 7V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-2M6 7h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2Z" /></svg>
        {{ copyLabel }}
      </button>
      <button class="ghost-button" type="button" :disabled="busy" @click="$emit('download')">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m0 0 5-5m-5 5-5-5M5 21h14" /></svg>
        {{ downloadLabel }}
      </button>
    </footer>
  </article>
</template>
