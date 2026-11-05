import asyncio

import pytest

from backend.infox.run_models import RunError
from tests.test_infox_publishing_manager import manager_for
from tests.infox_publishing_fakes import settle


def test_auto_copy_only_after_verified_upload(tmp_path):
    manager, run, fake, clipboard, input=manager_for(tmp_path,lost='upload')
    async def scenario():
        r=await manager.start(run.id,input); r=await settle(manager,r.id)
        with pytest.raises(RunError): await manager.copy_link(r.id,manual=True)
        assert not r.uploaded_verified and not clipboard.values
        await manager.close()
    asyncio.run(scenario())


def test_clipboard_failure_preserves_upload_success_and_manual_copy_never_uploads(tmp_path):
    manager,run,fake,clipboard,input=manager_for(tmp_path)
    clipboard.fail=True
    async def scenario():
        r=await manager.start(run.id,input); r=await settle(manager,r.id)
        assert r.stage=='clipboard_failed' and r.uploaded_verified
        before=list(fake.calls)
        clipboard.fail=False
        r=await manager.copy_link(r.id,manual=True)
        assert r.stage=='completed' and r.clipboard_state=='copied' and r.uploaded_verified
        assert fake.calls==before
        await manager.close()
    asyncio.run(scenario())


def test_restart_does_not_recopy_automatically(tmp_path):
    manager,run,fake,clipboard,input=manager_for(tmp_path)
    async def scenario():
        r=await manager.start(run.id,input); r=await settle(manager,r.id)
        r=manager.store.transition(r.id,r.version,{'stage':'copying_link','clipboard_state':'intent_recorded'})
        clipboard.values.clear()
        await manager.recover()
        assert clipboard.values==[]
        assert manager.store.get(r.id).stage=='clipboard_failed'
        assert manager.store.get(r.id).uploaded_verified
        await manager.close()
    asyncio.run(scenario())
