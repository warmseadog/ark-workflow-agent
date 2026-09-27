# Visible prompt/material synchronization

User requirement: inspect current task, tailor prompt templates to the task, and visibly update the prompt when a hairstyle reference is uploaded.
Observed current draft: one person image, one clothing image, one enabled hairstyle, no scene image. Hairstyle therefore maps to Image3.
Design: preserve editable base prompt, replace one generated material-role block on reference add/remove/enable and template application; generate numbers from existing provider ordering; save/restore full draft while reusable templates omit generated block. Backend strips the block and derives roles from immutable snapshot, so stale image numbers are not submitted.
Templates: update untouched bundled templates to fashion/action recreation instructions, preserve user-edited/deleted defaults and custom templates. One-time migration marker. Update current draft only if it still matches an untouched bundled base, using revision checks.
Constraints: no new model calls; no changes to submitted run snapshots; original server site/config untouched; actual service-account write check before deployment.
Verification ledger:
- API template tests failed before implementation for missing migration and pinned generated numbers; now green.
- Browser new assertions failed before implementation: prompt stayed unchanged on hairstyle upload; now 1440 and 390 pass dynamic numbering, template switch, delete, enable, and reload.
- Full regression and independent review pending.

- Review found and fixed editing caret jumps during autosave, generated block not dirtied after copying old snapshots, and frontend normalization of custom body. Autosave is read-only; blur/reference/template actions regenerate; custom body text remains unchanged. Plain draft restores reset summary badge.
- Browser red reproduction confirmed caret issue; final 1440/390 tests cover typing/pause, custom text preservation, immediate submit after copy matching visible prompt, and badge reset. Independent re-review confirms all findings fixed.
- Prompt budget: red test confirmed generated block could exceed old 10000 total. Now body remains limited to10000, total including managed block bounded14000. Full suite312passed before final JS-only fixes; final rerun pending.
- Final verification after review fixes:312pytest passed (existing Starlette warning); old browser flow1440/390 and prompt-sync1440/390 pass. No paid model calls.
- Deployment complete:e1707bf isolated release; current untouched draft base upgraded by revision, frontend saved Image3 hairstyle block. Public1440/390 verified saved-visible equality, media and historical snapshot preservation. No model call; original site/config unchanged.
