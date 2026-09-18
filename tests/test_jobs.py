from app.jobs import JobStore


def test_job_store_lifecycle():
    store = JobStore()
    job = store.create()
    assert job.status == "queued"
    store.update(job.id, status="running", progress=50, message="处理中")
    store.log(job.id, "step complete")
    public = store.get(job.id).public()
    assert public["status"] == "running"
    assert public["progress"] == 50
    assert public["logs"] == ["step complete"]
    assert public["download_url"] is None
