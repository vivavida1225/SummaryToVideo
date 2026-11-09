<script setup lang="ts">
import type { ProgressDisplay } from '../infoxFlow'

defineProps<{ progress: ProgressDisplay; name: string }>()
</script>

<template>
  <div class="infox-progress">
    <template v-if="progress.value != null">
      <progress :value="progress.value" max="100" :aria-label="name" />
      <p>{{ progress.label }} · {{ progress.value }}%</p>
    </template>
    <template v-else>
      <span class="infox-progress-indeterminate" aria-hidden="true"><i /></span>
      <p>{{ progress.label }} · 진행률 확인 중</p>
    </template>
  </div>
</template>
