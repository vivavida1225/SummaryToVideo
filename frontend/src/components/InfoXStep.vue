<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { stepStatusLabels, type InfoxStep } from '../infoxFlow'

// Body stays mounted (v-show) so inputs, focus targets and child state survive
// collapse. A user toggle wins over the automatic open state, so polling that
// changes the status never re-collapses a record the user opened.
const props = defineProps<{ step: InfoxStep; current: boolean; summary?: string; forceOpen?: boolean }>()
const userOpen = ref<boolean | null>(null)
const autoOpen = computed(() => props.current || !!props.forceOpen ||
  ['action', 'attention', 'failed'].includes(props.step.status))
const open = computed(() => props.step.status !== 'waiting' && (userOpen.value ?? autoOpen.value))
const bodyId = computed(() => `infox-step-${props.step.key}`)
// A step that newly becomes current is shown again even if the user closed it earlier.
watch(() => props.current, current => { if (current) userOpen.value = null })
function toggle() { userOpen.value = !open.value }
</script>

<template>
  <section class="infox-step" :data-status="step.status" :data-current="current || undefined" :aria-labelledby="bodyId + '-title'">
    <header class="infox-step-head">
      <span class="infox-step-number" aria-hidden="true">{{ step.number }}</span>
      <h4 :id="bodyId + '-title'">{{ step.title }}</h4>
      <span class="infox-badge" :data-status="step.status">{{ stepStatusLabels[step.status] }}</span>
      <button v-if="step.status !== 'waiting'" type="button" class="infox-step-toggle"
        :aria-expanded="open" :aria-controls="bodyId" @click="toggle">
        {{ open ? '접기' : '펼치기' }}<span class="sr-only">{{ ' ' + step.title }}</span>
      </button>
    </header>
    <p v-if="summary && !open" class="infox-step-summary">{{ summary }}</p>
    <div v-show="open" :id="bodyId" class="infox-step-body">
      <slot />
    </div>
  </section>
</template>
