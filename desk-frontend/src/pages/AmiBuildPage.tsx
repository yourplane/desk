import { useCallback, useEffect, useState } from 'react'
import {
  createAmiRecipe,
  listAmiBuilds,
  listAmiRecipes,
  startAmiBuild,
  type AmiBuild,
  type AmiRecipe,
} from '../api/client'

const EXAMPLE_RECIPE = `{
  "ami_name": "my-desk-ami",
  "instance_type": "t3.medium",
  "steps": [
    { "run": "set -euo pipefail\\necho hello from cloud AMI build" }
  ]
}`

export function AmiBuildPage() {
  const [recipes, setRecipes] = useState<AmiRecipe[]>([])
  const [builds, setBuilds] = useState<AmiBuild[]>([])
  const [recipeName, setRecipeName] = useState('My recipe')
  const [recipeJson, setRecipeJson] = useState(EXAMPLE_RECIPE)
  const [selectedRecipeId, setSelectedRecipeId] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const [r, b] = await Promise.all([listAmiRecipes(), listAmiBuilds()])
      setRecipes(r)
      setBuilds(b)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    if (!selectedRecipeId && recipes.length > 0) {
      setSelectedRecipeId(recipes[0].recipe_id)
    }
  }, [recipes, selectedRecipeId])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    const id = window.setInterval(() => {
      void load()
    }, 8000)
    return () => window.clearInterval(id)
  }, [load])

  async function saveRecipe() {
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      let body: Record<string, unknown>
      try {
        body = JSON.parse(recipeJson) as Record<string, unknown>
      } catch {
        throw new Error('Recipe JSON is invalid.')
      }
      const created = await createAmiRecipe(recipeName.trim() || 'Untitled', body)
      setMessage(`Saved recipe ${created.recipe_id}`)
      setSelectedRecipeId(created.recipe_id)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function runBuild() {
    if (!selectedRecipeId) {
      setError('Select a recipe first.')
      return
    }
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      const out = await startAmiBuild(selectedRecipeId)
      setMessage(`Started build ${out.build_id}`)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ami-build-page">
      <h1 className="page-title">AMI build</h1>
      <p className="page-lead">
        Define a recipe (same shape as <code>desk ami build</code>). Copy steps must use{' '}
        <code>s3://&lt;your desk data bucket&gt;/...</code> URIs. The workflow runs on Step Functions:
        create builder EC2 → SSM steps → register AMI → terminate builder.
      </p>

      {error && <p className="form-error">{error}</p>}
      {message && <p className="form-success">{message}</p>}

      <section className="ami-build-section">
        <h2>Recipe</h2>
        <label className="field-label">
          Name
          <input
            className="field-input"
            value={recipeName}
            onChange={(e) => setRecipeName(e.target.value)}
            disabled={busy}
          />
        </label>
        <label className="field-label">
          Recipe JSON
          <textarea
            className="field-textarea"
            rows={14}
            value={recipeJson}
            onChange={(e) => setRecipeJson(e.target.value)}
            spellCheck={false}
            disabled={busy}
          />
        </label>
        <div className="button-row">
          <button type="button" className="btn btn-start" disabled={busy} onClick={() => void saveRecipe()}>
            Save recipe
          </button>
        </div>
      </section>

      <section className="ami-build-section">
        <h2>Run build</h2>
        <label className="field-label">
          Saved recipe
          <select
            className="field-input"
            value={selectedRecipeId}
            onChange={(e) => setSelectedRecipeId(e.target.value)}
            disabled={busy}
          >
            <option value="">—</option>
            {recipes.map((r) => (
              <option key={r.recipe_id} value={r.recipe_id}>
                {r.name || r.recipe_id}
              </option>
            ))}
          </select>
        </label>
        <div className="button-row">
          <button type="button" className="btn btn-start" disabled={busy || !selectedRecipeId} onClick={() => void runBuild()}>
            Start AMI build
          </button>
        </div>
      </section>

      <section className="ami-build-section">
        <h2>Builds</h2>
        {builds.length === 0 ? (
          <p>No builds yet.</p>
        ) : (
          <table className="ami-build-table">
            <thead>
              <tr>
                <th>Build</th>
                <th>Recipe</th>
                <th>Status</th>
                <th>AMI</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {builds.map((b) => (
                <tr key={b.build_id}>
                  <td className="mono">{b.build_id.slice(0, 8)}…</td>
                  <td>{b.recipe_name || b.recipe_id.slice(0, 8)}</td>
                  <td>{b.status}</td>
                  <td className="mono">{b.ami_id || '—'}</td>
                  <td>{b.updated_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
