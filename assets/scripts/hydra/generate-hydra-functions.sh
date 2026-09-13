cp src/glsl/glsl-functions.js gf.mjs # force ESM; the repo's .js may not resolve as one
node -e "import('./gf.mjs').then(m=>console.log(JSON.stringify(m.default(),null,2)))" \
  >hydra-functions.json
