import { describe, expect, it, vi } from 'vitest'
import { EditorNavigation, editorEligible, editorUrl } from './infoxEditor'
import type { InfoXRun } from './types'

const run = { id: 'a'.repeat(32), video_id: 'video-1', stage: 'ready_for_editor', requires_reconciliation: false,
  asset_save: { state: 'unchanged_verified' } } as InfoXRun
function setup(blocked = false) {
  let version = 0
  const tab = { closed: false, opener: {}, document: { title: '', body: { textContent: '' } },
    location: { replace: vi.fn() }, close: vi.fn() }
  const port = { claimEditor: vi.fn(async (id, body) => ({ claimed: true, attempt: {
    id: body.attempt_id, run_id: id, version: 0, state: blocked ? 'blocked' : 'waiting', editor_status: 'unconfirmed',
  } })), updateEditor: vi.fn(async (id, attempt, body) => ({ id: attempt, run_id: id,
    version: ++version, state: body.state, video_id: run.video_id, editor_url: editorUrl(run), editor_status: 'unconfirmed' })),
    latestEditor: vi.fn(async () => ({ attempt: null })) }
  const open = vi.fn(() => blocked ? null : tab)
  return { nav: new EditorNavigation(port as never, open as never), tab, port, open }
}
describe('verified editor navigation', () => {
  it('requires all gates and constructs only a fixed origin URL', () => {
    expect(editorEligible(run)).toBe(true)
    for (const patch of [{ stage: 'verifying_assets' }, { requires_reconciliation: true }, { asset_save: { state: 'verifying' } }]) {
      expect(editorEligible({ ...run, ...patch } as InfoXRun)).toBe(false)
    }
    expect(editorUrl({ ...run, video_id: '../evil?token=x' })).toBe(null)
    expect(editorUrl({ ...run, editor_url: 'https://evil.invalid' })).toBe('https://infox2.infomaxai.com/studio/library/videos/video-1?chatboxView=script')
  })
  it('opens synchronously, waits for verification and navigates once despite repeated polls', async () => {
    const { nav, tab, port, open } = setup()
    const claim = nav.begin(run.id, 'automatic')
    expect(open).toHaveBeenCalledTimes(1)
    await claim
    await nav.advance({ ...run, asset_save: { state: 'verifying', receipt_state: 'not_sent' } })
    expect(tab.location.replace).not.toHaveBeenCalled()
    await Promise.all([nav.advance(run), nav.advance(run)])
    await nav.advance(run)
    expect(tab.location.replace).toHaveBeenCalledExactlyOnceWith(editorUrl(run))
    expect(port.updateEditor.mock.calls.map(call => call[2].state)).toEqual(['navigate_intent', 'navigation_requested'])
    expect(nav.attempt.value?.editor_status).toBe('unconfirmed')
  })
  it('restores by GET only and never auto opens even a previously waiting attempt', async () => {
    const { nav, port, open } = setup()
    port.latestEditor.mockResolvedValue({ attempt: { id: 'b', run_id: run.id, state: 'navigate_intent', version: 1 } } as never)
    await nav.restore(run.id); await nav.advance(run)
    expect(open).not.toHaveBeenCalled(); expect(port.updateEditor).not.toHaveBeenCalled()
    expect(nav.message.value).toContain('불명확')
  })
  it('records popup blocking without opening again on polling', async () => {
    const { nav, open } = setup(true)
    await nav.begin(run.id, 'automatic'); await nav.advance(run)
    expect(nav.attempt.value?.state).toBe('blocked')
    expect(nav.message.value).toContain('팝업')
    expect(open).toHaveBeenCalledTimes(1)
  })
  it('detects a closed waiting tab and permits explicit reopening', async () => {
    const { nav, tab, open } = setup()
    await nav.begin(run.id, 'automatic'); tab.closed = true
    await nav.advance(run)
    expect(nav.attempt.value?.state).toBe('closed'); expect(tab.location.replace).not.toHaveBeenCalled()
    tab.closed = false
    await nav.begin(run.id, 'manual'); await nav.advance(run)
    expect(open).toHaveBeenCalledTimes(2); expect(tab.location.replace).toHaveBeenCalledTimes(1)
  })
  it('rejects another owner and closes only the unused waiting tab', async () => {
    const { nav, tab, port } = setup()
    port.claimEditor.mockResolvedValue({ claimed: false, attempt: { id: 'other', state: 'waiting', version: 0 } } as never)
    await nav.begin(run.id, 'automatic'); await nav.advance(run)
    expect(tab.close).toHaveBeenCalledTimes(1); expect(tab.location.replace).not.toHaveBeenCalled()
  })
  it('a lost navigate acknowledgement stays uncertain and is never retried', async () => {
    const { nav, tab, port } = setup()
    await nav.begin(run.id, 'automatic')
    port.updateEditor.mockRejectedValue(new Error('network'))
    await nav.advance(run); await nav.advance(run)
    expect(tab.location.replace).not.toHaveBeenCalled(); expect(port.updateEditor).toHaveBeenCalledTimes(1)
    expect(nav.message.value).toContain('불명확')
  })
  it('reports assignment failure separately from server readiness', async () => {
    const { nav, tab } = setup()
    tab.location.replace.mockImplementation(() => { throw new Error('navigation failed') })
    await nav.begin(run.id, 'automatic'); await nav.advance(run)
    expect(nav.attempt.value?.state).toBe('failed'); expect(run.stage).toBe('ready_for_editor')
  })
  it('does not navigate an unrelated run', async () => {
    const { nav, tab } = setup()
    await nav.begin(run.id, 'automatic'); await nav.advance({ ...run, id: 'other' })
    expect(tab.location.replace).not.toHaveBeenCalled()
  })
  it('does not acquire another tab while a navigation intent is in flight', async () => {
    const { nav, port, open } = setup()
    await nav.begin(run.id, 'automatic')
    const original = port.updateEditor.getMockImplementation()!
    let release!: () => void
    port.updateEditor.mockImplementationOnce(async (...args) => {
      await new Promise<void>(resolve => { release = resolve })
      return original(...args)
    })
    const moving = nav.advance(run)
    await nav.begin(run.id, 'manual')
    expect(open).toHaveBeenCalledTimes(1)
    release(); await moving
  })
  it('a late restore cannot overwrite a newer click and navigation result', async () => {
    const { nav, port } = setup()
    let release!: (value: { attempt: null }) => void
    port.latestEditor.mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
    const restoring = nav.restore(run.id)
    await nav.begin(run.id, 'automatic'); await nav.advance(run)
    release({ attempt: null }); await restoring
    expect(nav.attempt.value?.state).toBe('navigation_requested')
  })
  it('an old report response cannot populate a different job after reset', async () => {
    const { nav, port } = setup()
    await nav.begin(run.id, 'automatic'); await nav.advance(run)
    const original = port.updateEditor.getMockImplementation()!
    let release!: () => void
    port.updateEditor.mockImplementationOnce(async (...args) => {
      await new Promise<void>(resolve => { release = resolve }); return original(...args)
    })
    const reporting = nav.report('login_required')
    nav.reset(); release(); await reporting
    expect(nav.attempt.value).toBe(null)
  })
  it('a failed claim from an old job cannot set the new jobs error', async () => {
    const { nav, port } = setup()
    let reject!: (reason: Error) => void
    port.claimEditor.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail }))
    const claiming = nav.begin(run.id, 'automatic')
    nav.reset(); reject(new Error('old network failure')); await claiming
    expect(nav.error.value).toBe('')
    expect(nav.attempt.value).toBe(null)
  })
})
