<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import type { DownloadRecord } from '../types'

const props = defineProps<{publicationId: string; uploadedVerified: boolean}>()
const records = ref<DownloadRecord[]>([])
const loading = ref(true)
const busy = ref(false)
const uncertain = ref(false)
const error = ref('')
const notice = ref('')
const active = computed(() => records.value.some(r => ['queued','pending','running'].includes(r.state)))
const latest = computed(() => records.value[0])
const automatic = computed(() => records.value.find(r => r.automatic && r.id !== latest.value?.id))
const shown = computed(() => [latest.value,automatic.value].filter((r): r is DownloadRecord => !!r))
const states: Record<string,string> = {queued:'진행 중인 파일 다운로드 완료 대기',pending:'다운로드 준비 중',running:'다운로드 중',completed:'다운로드 완료',
  partial:'일부 다운로드 완료',failed:'다운로드 실패',needs_attention:'다운로드 확인 필요'}
const itemStates: Record<string,string> = {pending:'대기',downloading:'저장 중',completed:'저장 완료',failed:'저장 실패'}
const errors: Record<string,string> = {timeout:'전송 시간이 초과되었습니다.',network_error:'다운로드 연결이 끊겼습니다.',
  permission_denied:'파일 저장 권한이 없습니다.',disk_full:'저장 공간이 부족합니다.',
  downloads_folder_unavailable:'사용자 기본 다운로드 폴더를 확인할 수 없습니다.',source_missing:'원본 파일이 없습니다.',
  render_file_mismatch:'해당 회차의 렌더 파일을 확인할 수 없습니다.',server_restarted:'서버가 재시작되어 다운로드가 중단되었습니다.',
  auth_expired:'InfoX 인증을 확인하세요.',invalid_media:'정상 MP4 파일을 받지 못했습니다.',invalid_response:'InfoX 다운로드 응답을 확인할 수 없습니다.'}
let epoch = 0
let generation = 0
let timer: ReturnType<typeof setTimeout> | undefined
type Pending = { intent_id: string; retry_id?: string }
let pending: Pending | undefined
const storageKey = () => `infox-download-intent:${props.publicationId}`
// Poll even before the automatic operation exists: render completion can occur later.
async function load(current: number) {
  const revision = generation
  try {
    const result = await api.infoxDownloads(props.publicationId)
    if (current !== epoch || revision !== generation) return
    records.value = result.downloads
    error.value = ''
  } catch {
    if (current === epoch && revision === generation) error.value = '다운로드 상태 조회 실패 · 잠시 후 다시 확인합니다.'
  } finally {
    if (current === epoch && revision === generation) {
      loading.value = false
      timer = setTimeout(() => { void load(current) }, 2000)
    }
  }
}
watch(() => props.publicationId, () => {
  const current = ++epoch
  generation++
  clearTimeout(timer)
  records.value=[]; loading.value=true; busy.value=false; uncertain.value=false; error.value=''
  notice.value=''
  pending=undefined
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey()) || 'null') as Pending | null
    if (value && typeof value.intent_id === 'string' && (!value.retry_id || typeof value.retry_id === 'string')) {
      pending=value; uncertain.value=true
    }
  } catch { /* A new request must successfully persist its intent before sending. */ }
  void load(current)
}, {immediate:true})
onBeforeUnmount(() => { epoch++; clearTimeout(timer) })

async function save(retry?: DownloadRecord, replay = false) {
  if (busy.value || loading.value || (!replay && (active.value || uncertain.value))) return
  if (replay && !pending) return
  if (!replay && !retry && !props.uploadedVerified) return
  busy.value = true
  generation++
  const current = epoch
  clearTimeout(timer)
  try {
    if (!pending) pending = {intent_id:crypto.randomUUID(),retry_id:retry?.id}
    try {
      sessionStorage.setItem(storageKey(),JSON.stringify(pending))
    } catch {
      error.value='다운로드 요청 기록을 보관할 수 없습니다. 브라우저 저장소 설정을 확인하세요.'
      pending=undefined
      return
    }
    const result = pending.retry_id ? await api.retryInfoxDownload(pending.retry_id,pending.intent_id) :
      await api.startInfoxDownload(props.publicationId,{intent_id:pending.intent_id,kind:'all'})
    if (current !== epoch) return
    records.value = [result,...records.value.filter(r => r.id !== result.id)]
    notice.value = !pending.retry_id && result.kind !== 'all' ?
      '기존 영상 다운로드가 진행 중입니다. 완료 후 3종 다운로드 버튼을 다시 눌러 주세요.' : ''
    pending=undefined; uncertain.value=false
    try { sessionStorage.removeItem(storageKey()) } catch { /* Durable server intent remains idempotent. */ }
    error.value = ''
  } catch {
    if (current === epoch) {
      uncertain.value = true
      error.value = ''
    }
  } finally {
    if (current === epoch) {
      busy.value = false
      timer = setTimeout(() => { void load(current) }, 2000)
    }
  }
}
</script>

<template>
  <section aria-label="파일 다운로드" class="infox-downloads">
    <p>저장 위치: 사용자 기본 다운로드 폴더</p>
    <button v-if="uploadedVerified" type="button" class="ghost-button"
      :disabled="loading || busy || active || uncertain" @click="save()">대본·원본·영상 3개 파일 다운로드</button>
    <template v-if="uncertain">
      <p>다운로드 접수 결과를 확인하지 못했습니다. 같은 요청으로 접수 여부를 다시 확인하세요.</p>
      <button type="button" class="ghost-button" :disabled="loading || busy" @click="save(undefined,true)">다운로드 접수 다시 확인</button>
    </template>
    <div v-for="record in shown" :key="record.id" aria-live="polite">
      <p v-if="record.automatic">{{ record.kind === 'all' ? '렌더 완료 대본·원본·영상 자동 다운로드' : '렌더 완료 영상 자동 다운로드' }}</p>
      <p role="status">{{ states[record.state] }}</p>
      <p v-if="record.error_code">{{ errors[record.error_code] ?? '파일 저장에 실패했습니다. 다시 다운로드하세요.' }}</p>
      <ul>
        <li v-for="item in record.items" :key="item.kind">
          <p>{{ item.filename }} · {{ itemStates[item.state] }}</p>
          <p v-if="item.path" class="download-path">{{ item.path }}</p>
          <p v-if="item.error_code">{{ errors[item.error_code] ?? '파일 저장에 실패했습니다. 다시 다운로드하세요.' }}</p>
        </li>
      </ul>
      <button v-if="!['queued','pending','running'].includes(record.state)" type="button" class="ghost-button"
        :disabled="loading || busy || active || uncertain" @click="save(record)">
        {{ record.state === 'completed' ? '파일 확인·다시 다운로드' : '실패 파일 다시 다운로드' }}
      </button>
    </div>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
  </section>
</template>

<style scoped>
.download-path { overflow-wrap: anywhere; }
</style>
