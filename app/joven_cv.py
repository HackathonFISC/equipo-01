"""Ingreso del joven con CV: archivo efímero, Perfil con evidencia y matching real."""
import json

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from puente.guardrails import evaluar_entrada, limpiar_entrada


async def endpoint_cv(request):
    from app import server as srv

    try:
        tipo, contenido, campos = await srv.leer_archivo_subido(request, srv.ESTADO_GLOBAL, max_campo=16000)
        try:
            datos = json.loads(campos.get("datos", "{}"))
            if not isinstance(datos, dict):
                raise ValueError()
            req = srv.PerfilJovenRequest.model_validate({**datos, "respuestas": ["CV adjunto"]})
            entrada = srv.preparar_entrada_joven(req)
        except ValidationError as exc:
            return JSONResponse({"detail": exc.errors(include_context=False, include_input=False)}, status_code=422)
        except (ValueError, srv.ErrorValidacionTablero):
            raise srv.ErrorSubida("Revisa tus datos y elige un corregimiento de la lista.", 422)

        texto = await run_in_threadpool(srv.leer_texto_de_cv, tipo, contenido)
        if len("".join(texto.split())) < srv.MIN_CARACTERES_CV:
            raise srv.ErrorSubida("No pudimos leer tu CV. Prueba una foto más clara, un PDF con texto o completa tus datos.")
        texto = str(limpiar_entrada(texto))
        for revision in evaluar_entrada({"texto": texto, "edad": req.edad}):
            srv._registrar_decision_db(srv.ESTADO_GLOBAL, revision.decision)
            if not revision.ok:
                raise srv.ErrorSubida(revision.respuesta or revision.motivo)
        entrada.pop("respuestas", None)
        entrada["cv_texto"] = texto
        taxonomia = srv.cargar_taxonomia_grafo()
        try:
            perfil, decision = await run_in_threadpool(
                srv.construir_perfil, entrada, taxonomia=taxonomia,
                normalizar=srv.normalizar_habilidad, llm=srv.llm_perfil_joven,
            )
        finally:
            entrada.pop("cv_texto", None)
        if not perfil.habilidades:
            raise srv.ErrorSubida("No encontramos habilidades con evidencia en el CV. Completa tus datos para contarnos tu experiencia.")
        srv.registrar_perfil_desde_cv(srv.ESTADO_GLOBAL, perfil, decision, taxonomia, origen="joven")
        token = srv._crear_token_joven(srv.ESTADO_GLOBAL, perfil.id)
        return JSONResponse({**srv.resultado_joven(srv.ESTADO_GLOBAL, perfil.id), "token": token}, status_code=201)
    except srv.ErrorSubida as exc:
        return srv._respuesta_subida(exc)
    except ValueError:
        return JSONResponse({"detail": "No pudimos analizar el CV. Prueba otro archivo o completa tus datos."}, status_code=422)
    except (RuntimeError, OSError) as exc:
        mensaje = "El análisis del CV no está disponible ahora. Puedes completar tus datos o intentarlo más tarde."
        if getattr(exc, "causa", "") == "grabacion_faltante":
            mensaje = "Este CV necesita análisis en línea. El servidor está en modo sin conexión; puedes usar el ejemplo de Yaritza."
        return JSONResponse({"detail": mensaje}, status_code=503)
