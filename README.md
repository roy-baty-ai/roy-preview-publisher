# roy-preview-publisher
Static preview publisher for approved public preview assets.

Allowed Preview branches: `glass-green`, `reamertec`, `window-door-demo`. Public static files live under the matching `preview-assets/<branch>/` directory on `preview-content`. The shared `window-door-demo` contains a fictional window/door contractor demo, not a real company site.

## Manual workflow installed; activation incomplete

The reviewed publisher and dispatch-only workflow are installed. Protection and Environment configuration must be verified before the owner stores a token. Do not dispatch before protection, approved content and owner-secret gates pass.

See [setup, trust boundaries and local tests](tools/pages-preview/README.md). No site assets or credentials are included.
