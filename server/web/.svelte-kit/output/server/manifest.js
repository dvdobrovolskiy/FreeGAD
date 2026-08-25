export const manifest = (() => {
function __memo(fn) {
	let value;
	return () => value ??= (value = fn());
}

return {
	appDir: "_app",
	appPath: "_app",
	assets: new Set([]),
	mimeTypes: {},
	_: {
		client: {start:"_app/immutable/entry/start.BYdz_qo_.js",app:"_app/immutable/entry/app.BG6zVLoQ.js",imports:["_app/immutable/entry/start.BYdz_qo_.js","_app/immutable/chunks/iF58TQOT.js","_app/immutable/chunks/9caWUa63.js","_app/immutable/chunks/BN9Qj8xE.js","_app/immutable/chunks/B2dS1RzF.js","_app/immutable/entry/app.BG6zVLoQ.js","_app/immutable/chunks/9caWUa63.js","_app/immutable/chunks/BFKk8biB.js","_app/immutable/chunks/BjyU-JeS.js","_app/immutable/chunks/B2dS1RzF.js","_app/immutable/chunks/Drg8tlqI.js","_app/immutable/chunks/CQuCceuX.js"],stylesheets:[],fonts:[],uses_env_dynamic_public:false},
		nodes: [
			__memo(() => import('./nodes/0.js')),
			__memo(() => import('./nodes/1.js')),
			__memo(() => import('./nodes/2.js')),
			__memo(() => import('./nodes/3.js'))
		],
		remotes: {
			
		},
		routes: [
			{
				id: "/",
				pattern: /^\/$/,
				params: [],
				page: { layouts: [0,], errors: [1,], leaf: 2 },
				endpoint: null
			},
			{
				id: "/login",
				pattern: /^\/login\/?$/,
				params: [],
				page: { layouts: [0,], errors: [1,], leaf: 3 },
				endpoint: null
			}
		],
		prerendered_routes: new Set([]),
		matchers: async () => {
			
			return {  };
		},
		server_assets: {}
	}
}
})();
