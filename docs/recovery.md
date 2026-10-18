# Recuperación de generación estructural

deploy_report_issue(base_url, model, error_class, reason) informa de un síntoma a Prometheus.
URL/model deben coincidir exactamente con una receta de chat gestionada y en marcha.
error_class admite degenerate_output o structurally_invalid_output; length, fences y errores
lógicos aislados no son motivos de recarga. La respuesta rápida lleva id, status, recipe, boot,
job y deduplicated. deploy_recovery_status(recovery_id, recipe) consulta estado, canarios y alcance;
job_get/job_cancel conservan sus funciones. Se reutiliza Jobs, no un monitor paralelo.

El destino se comprueba contra archivos/ajustes y estado local antes de cualquier petición:
no se prueban URLs ajenas, detected ni external. El worker toma model:principal en Ágora y un
lease renovable del Hub como prometheus. Si están ocupados o no disponibles deja visible
waiting_coordination; no usa identidad Codex, otro backend ni fallback local del lease.
Tras comprobar ownership/readiness solicita dos canarios de código distintos. Sólo dos STOP
completos con Python estructuralmente roto o repetición consecutiva sostenida confirman el síntoma.
Truncamiento, transporte, formato mixto o manifiesto incorrecto dejan not_confirmed y no recargan.

Espera métricas nuevas con running=waiting=0; ausencia de métricas no significa inactividad.
Antes del stop revalida receta, boot, hashes de todos sus archivos, coordinación, ownership y carga.
Recipes.restart utiliza el lifecycle existente. Su guard opcional before_stop corre después de
reservar la operación y esperar starts previos, antes de cambiar a stopping; si rechaza, conserva
la ejecución anterior. No se reescribe la receta ni se descargan pesos o modifican datos.

Una vez lanzado el lifecycle, el supervisor lo sigue hasta un estado estable aunque se cancele
el trabajo, se cierre la app o falle una renovación. No libera voluntariamente el contexto de
coordinación mientras su worker sigue operando. Un error de renovación se refleja como
coordination_confirmed=false; no se presume que un lock o lease caducado siga vigente.
El callback before_start se ejecuta tras las comprobaciones previas de carga y antes de registrar
el worker de arranque: exige coordinación fresca, la misma receta/boot/archivos y ausencia de
cancelación. Si falla después de descargar, no carga y el lifecycle termina failed. Si la carga
ya había empezado, la cancelación espera su final antes de soltar el contexto; no abandona ranks
a mitad del lifecycle. Un fallo de coordinación en el postcheck deja unverified, sin volver a
confirmar ni iniciar otra recarga.

Después se comprueban hashes y ambos canarios. Sólo dos PASS producen recovered; los demás casos
quedan unverified/failed. Un trabajo activo se deduplica. Un cooldown por receta cubre también
el cambio de boot y no presenta recovered histórico como verificación de un síntoma nuevo.
Al reiniciar Prometheus, un trabajo activo queda interrupted; no reanuda una recarga a ciegas.

## Alcance explícito

Las respuestas incluyen verified_scope=structural_generation_and_literal_manifest,
algorithm_semantics_verified=false y canaries_discriminatively_validated=false.

Cada canario pide un módulo completo: función de journal o planificación, asserts, constante
CANARY_RESULT con resultados de varios fixtures y marcador final CANARY_COMPLETE=True.
La CPU analiza AST y ast.literal_eval de esa constante frente a un oráculo tipado independiente.
Nunca ejecuta funciones ni asserts generados. Un literal correcto comprueba datos, no que la
función implemente el algoritmo. Fences alrededor de un módulo válido no son corrupción.

recovered significa que el síntoma estructural no reapareció en esos dos canarios tras recargar.
No significa programa correcto, salud semántica completa, recuperación de herramientas con
efectos ni estabilidad certificada. Este flujo no repite herramientas del cliente.

Son canarios candidatos, probados con mocks y regresiones CPU. No está demostrada su capacidad
discriminativa en el estado malo real: las respuestas conservadas de ese estado terminaron en
length con presupuesto1600 y no hay rescore anterior a recargar. No se inventa un ensayo
retrospectivo. Para elevar esa garantía se necesita un fallo STOP real conservado, comparar
ambos canarios antes/después con la misma configuración y revisar falsos positivos.

## Coordinación y límites

Lock y lease coordinan participantes que los respetan. Un lock local o running=0 no bloquea
el HTTP directo de terceros a vLLM. El guard revalida antes del stop; no ofrece admisión atómica
de peticiones ajenas. Un cambio de receta/boot cancela; una edición externa de archivos durante
el lifecycle deja failed, nunca recovered. No se afirma exclusión de clientes externos.

Tests y demo no hacen inferencia, SSH ni reinicios reales: clúster inventado, callbacks y transportes
simulados. La UI conserva el listado normal de Jobs; las herramientas REST/MCP nativas exponen los
detalles y el alcance exacto. El log del trabajo muestra el estado final y los tres campos de alcance,
sin confundir trabajo terminado con recuperación verificada. Los canarios tienen un presupuesto propio de QA; no limitan las
peticiones ni capacidades del usuario.
