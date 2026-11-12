import { computed, ref } from 'vue'
import type { api } from './api'
import type { PublicationRecord, PublishCharge } from './types'

const terminal = new Set(['completed', 'failed', 'needs_attention', 'clipboard_failed'])
export const publishStages: Record<string, string> = {
  prepared:'게시 준비', preflighting:'최신 저장 상태·게시 조건 확인 중',
  starting_render:'렌더 요청 중', rendering:'렌더 진행 중', generating_metadata:'유튜브 정보 생성 중',
  saving_metadata:'제목·설명 저장 및 재조회 검증 중', starting_upload:'일부공개 업로드 요청 중',
  uploading:'일부공개 업로드 진행 중', copying_link:'업로드 완료 · 링크 복사 중',
  completed:'업로드 완료 · 링크 복사 완료', failed:'게시 실패', needs_attention:'게시 확인 필요',
  clipboard_failed:'업로드 완료 · 링크 복사 실패',
}
export const publishErrors: Record<string, string> = {
  preflight_unconfirmed:'대상 영상의 저장 버전·권한·이용량을 확인하지 못했습니다.',
  existing_publication:'종료를 확인할 수 없는 원격 작업이 있습니다. 기존 작업을 확인한 뒤 다시 준비하세요.',
  publication_active:'이 영상에 진행 중인 작업이 있습니다. 완료 후 다시 준비하세요.',
  previous_unconfirmed:'이전 요청의 접수 여부 또는 종료 결과를 확인하지 못했습니다. 새 실행을 차단했습니다.',
  remote_changed:'최초 조회 후 편집 저장 버전이 변경되었습니다. 실행이 중단되었습니다.',
  stream_unavailable:'진행 채널 연결을 확인하지 못했습니다. 후속 요청을 중단했습니다.',
  stream_overflow:'진행 이벤트를 모두 확인할 수 없어 중단했습니다.',
  stream_unrecognized:'현재 작업의 진행 이벤트 형식을 확인하지 못했습니다.',
  remote_task_failed:'현재 작업의 실패 신호를 받았습니다. 후속 단계를 중단했습니다.',
  history_unrecognized:'작업별 완료 이력을 확인하지 못했습니다.',
  metadata_mismatch:'렌더 결과와 유튜브 정보의 연결 또는 일부공개 설정을 확인하지 못했습니다.',
  stage_timeout:'단계의 제한 시간이 지났습니다. 기존 작업의 결과를 확인해야 합니다.',
  acceptance_unknown:'요청 접수 여부가 불명확합니다. 같은 요청을 다시 보내지 않습니다.',
  authentication_required:'InfoX 인증 또는 권한을 확인하세요.',
  server_restarted:'서버가 재시작되어 자동 진행이 중단되었습니다. 기존 결과를 확인하세요.',
  clipboard_error:'업로드 결과는 확인되었습니다. 링크 다시 복사를 사용할 수 있습니다.',
  internal_error:'게시 상태 확인이 중단되었습니다. 기존 기록을 확인하세요.',
}
export function seoulToday(now = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(now)
  return ['year','month','day'].map(key=>parts.find(part=>part.type===key)!.value).join('-')
}
export function formatPublishMetadata(day: string) {
  const parsed = new Date(day+'T00:00:00Z')
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || !Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0,10)!==day) return null
  const [year,month,date]=day.split('-')
  return {title:`${year!.slice(-2)}${month}${date} AI 영상 시황`,
    description:`${year}년 ${Number(month)}월 ${Number(date)}일 AI 영상 시황`}
}

type Port = Pick<typeof api, 'startInfoxPublication'|'rerenderInfoxPublication'|'infoxPublicationHistory'|'latestInfoxPublication'|'infoxPublication'|'copyInfoxPublicationLink'|'infoxPublicationPreview'>
export class PublishingControl {
  readonly reviewConfirmed = ref(false)
  private reviewedVersion: string | null = null
  readonly record = ref<PublicationRecord|null>(null)
  readonly history = ref<PublicationRecord[]>([])
  readonly preparing = ref(false)
  readonly marketDate = ref('')
  readonly busy = ref(false)
  readonly loading = ref(false)
  readonly uncertain = ref(false)
  readonly error = ref('')
  readonly charge = ref<PublishCharge|null>(null)
  readonly canPrepare = computed(()=>!!this.record.value && terminal.has(this.record.value.stage) &&
    !this.preparing.value && !this.loading.value && !this.busy.value && !this.uncertain.value)
  readonly canStart = computed(()=>(!this.record.value || this.preparing.value) &&
    this.reviewConfirmed.value && !!this.reviewedVersion &&
    !this.loading.value && !this.busy.value && !this.uncertain.value &&
    !!formatPublishMetadata(this.marketDate.value) && this.charge.value?.available===true && this.charge.value.can_start===true)
  private runId: string|null = null
  private epoch = 0
  private draftEpoch = 0
  private previewAllowed = false
  private timer: ReturnType<typeof setTimeout>|undefined
  constructor(private port: Port, private initialMarketDate = seoulToday()) { this.marketDate.value=initialMarketDate }

  dispose() { this.epoch++; this.draftEpoch++; clearTimeout(this.timer); this.runId=null; this.resetReview() }

  private resetReview() { this.reviewConfirmed.value=false; this.reviewedVersion=null }

  async confirmReview(confirmed: boolean) {
    this.resetReview()
    if(!confirmed || !this.runId || !this.previewAllowed || this.loading.value || this.busy.value) return
    const epoch=this.epoch, draft=this.draftEpoch
    this.loading.value=true; this.error.value=''
    try {
      const preview=await this.port.infoxPublicationPreview(this.runId)
      if(epoch!==this.epoch || draft!==this.draftEpoch || !this.previewAllowed) return
      this.charge.value=preview
      if(preview.latest_publication_id!==(this.record.value?.id ?? null)) {
        this.error.value='게시 기록이 변경되었습니다. 최신 회차를 다시 불러오세요.'; return
      }
      if(preview.can_start && preview.remote_updated_at) {
        this.reviewedVersion=preview.remote_updated_at; this.reviewConfirmed.value=true
      } else this.error.value=publishErrors[preview.blocked_reason ?? ''] ?? '최신 저장본을 확인하지 못했습니다. 다시 확인하세요.'
    } catch { if(epoch===this.epoch) this.error.value='최신 저장본 조회에 실패했습니다. 검토 완료를 다시 확인하세요.' }
    finally { if(epoch===this.epoch) this.loading.value=false }
  }
  async restore(id: string, preview = true) {
    this.resetReview()
    const epoch=++this.epoch
    this.draftEpoch++
    clearTimeout(this.timer)
    this.runId=id; this.previewAllowed=preview; this.loading.value=true; this.record.value=null; this.charge.value=null
    this.history.value=[]; this.preparing.value=false
    this.marketDate.value=this.initialMarketDate; this.uncertain.value=false; this.error.value=''; this.busy.value=false
    try {
      const [result,history]=await Promise.all([this.port.latestInfoxPublication(id),this.port.infoxPublicationHistory(id)])
      if(epoch!==this.epoch) return
      this.history.value=history.publications
      this.apply(result.publication)
      if(!result.publication && this.previewAllowed) {
        const charge=await this.port.infoxPublicationPreview(id)
        if(epoch===this.epoch && this.previewAllowed) this.charge.value=charge
      }
    } catch { if(epoch===this.epoch) {this.error.value='게시 기록 조회 실패 · 최신 상태를 확인하려면 화면을 새로고침하세요.'; this.uncertain.value=true} }
    finally {if(epoch===this.epoch) this.loading.value=false}
  }
  async prepareNew() {
    if(!this.canPrepare.value || !this.runId) return
    this.resetReview()
    const epoch=this.epoch, draft=++this.draftEpoch, id=this.runId
    this.loading.value=true; this.error.value=''; this.charge.value=null
    try {
      const [latest,history]=await Promise.all([this.port.latestInfoxPublication(id),this.port.infoxPublicationHistory(id)])
      if(epoch!==this.epoch || draft!==this.draftEpoch) return
      this.history.value=history.publications; this.apply(latest.publication)
      if(!latest.publication || !terminal.has(latest.publication.stage)) return
      this.preparing.value=true
      this.marketDate.value=latest.publication.snapshot.market_date
      const preview=await this.port.infoxPublicationPreview(id)
      if(epoch!==this.epoch || draft!==this.draftEpoch) return
      if(preview.latest_publication_id!==this.record.value?.id) {
        this.preparing.value=false
        this.error.value='게시 기록이 변경되었습니다. 화면을 새로고침해 최신 회차를 확인하세요.'
        return
      }
      if(this.previewAllowed) this.charge.value=preview
    } catch {if(epoch===this.epoch && draft===this.draftEpoch) this.error.value='새 회차 준비 조회 실패 · 다시 준비하거나 화면을 새로고침하세요.'}
    finally {if(epoch===this.epoch && draft===this.draftEpoch) this.loading.value=false}
  }
  cancelPreparation() {
    this.resetReview()
    this.draftEpoch++; this.preparing.value=false; this.loading.value=false; this.charge.value=null; this.error.value=''
    if(this.record.value) this.marketDate.value=this.record.value.snapshot.market_date
  }
  async setEligibility(eligible: boolean) {
    this.previewAllowed=eligible
    if(!eligible) {this.charge.value=null; this.resetReview(); return}
    if(!this.runId || this.loading.value || this.record.value || this.uncertain.value) return
    const epoch=this.epoch
    this.loading.value=true
    try {
      const charge=await this.port.infoxPublicationPreview(this.runId)
      if(epoch===this.epoch && this.previewAllowed && !this.record.value) this.charge.value=charge
    } catch {if(epoch===this.epoch) this.error.value='렌더 이용량 조회 실패 · 화면을 새로고침해 확인하세요.'}
    finally {if(epoch===this.epoch) this.loading.value=false}
  }
  private apply(record: PublicationRecord|null) {
    this.record.value=record
    if(record) {
      if(!this.preparing.value) this.marketDate.value=record.snapshot.market_date
      this.history.value=[record,...this.history.value.filter(r=>r.id!==record.id)]
        .sort((a,b)=>b.attempt_number-a.attempt_number)
    }
    if(record && !terminal.has(record.stage)) this.schedule()
  }
  private schedule() {
    clearTimeout(this.timer)
    const epoch=this.epoch
    this.timer=setTimeout(()=>{void this.poll(epoch)},1000)
  }
  private async poll(epoch: number) {
    const id=this.record.value?.id
    if(!id || epoch!==this.epoch) return
    try {
      const r=await this.port.infoxPublication(id)
      if(epoch!==this.epoch || r.id!==this.record.value?.id) return
      this.error.value=''; this.apply(r)
    } catch { if(epoch===this.epoch) {this.error.value='진행 조회 실패 · 기존 실행을 유지하며 다시 조회합니다.'; this.schedule()} }
  }
  async start() {
    if(!this.canStart.value || !this.runId) return
    this.busy.value=true; this.uncertain.value=true; this.error.value=''
    const epoch=this.epoch, id=this.runId, previous=this.record.value?.id
    const day=this.marketDate.value
    const reviewed_remote_updated_at=this.reviewedVersion!
    this.resetReview()
    this.preparing.value=false; this.charge.value=null; this.draftEpoch++
    try {
      const input={intent_id:crypto.randomUUID(),market_date:day,review_confirmed:true,reviewed_remote_updated_at}
      const r=previous ? await this.port.rerenderInfoxPublication(id,{...input,previous_publication_id:previous}) :
        await this.port.startInfoxPublication(id,input)
      if(epoch===this.epoch) {this.uncertain.value=false; this.apply(r)}
    } catch (error) {
      if(epoch!==this.epoch) return
      this.error.value='접수 응답을 확인하지 못했습니다. 기존 게시 기록을 조회하며 요청을 다시 보내지 않습니다.'
      try {
        const result=await this.port.latestInfoxPublication(id)
        if(epoch===this.epoch) {
          const definitive=error instanceof Error && 'status' in error && typeof error.status==='number' && error.status>=400 && error.status<500
          if(result.publication && (result.publication.id!==previous || definitive)) {
            this.apply(result.publication); this.uncertain.value=false
            if(definitive) this.error.value='게시 상태가 변경되었거나 시작 조건을 충족하지 못했습니다. 최신 회차를 확인하고 다시 준비하세요.'
          }
        }
      } catch { /* Remain uncertain; never automatically replay the POST. */ }
    } finally {if(epoch===this.epoch) this.busy.value=false}
  }
  async copy() {
    const r=this.record.value
    if(!r || r.stage!=='clipboard_failed' || this.busy.value || this.loading.value || this.preparing.value || this.uncertain.value) return
    this.busy.value=true
    const epoch=this.epoch
    try {
      const updated=await this.port.copyInfoxPublicationLink(r.id)
      if(epoch===this.epoch) {this.error.value=''; this.apply(updated)}
    } catch {if(epoch===this.epoch) this.error.value='링크 복사 결과를 확인하지 못했습니다. 보관된 업로드 링크를 확인하세요.'}
    finally {if(epoch===this.epoch) this.busy.value=false}
  }
}
