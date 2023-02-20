from fastapi import APIRouter, Depends, status, HTTPException
from loguru import logger
from slugify import slugify
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.responses import CustomJSONResponse
from db.crud.crud_permissions import PermissionsDAL
from db.crud.crud_roles import RolesDAL
from db.crud.crud_roles_permissions import RolesPermissionsDAL
from dependencies.auth import AuthorizeTokenUser
from dependencies.database import get_session
from schemas.permissions import PermissionListResponseSchema, PermissionBaseSchema, PermissionCreateUpdateRequestSchema
from schemas.response import ErrorResponse, DefaultResponse
from schemas.token import TokenUser

router = APIRouter(prefix='/permissions', tags=['permissions'])


@router.get('',
            response_model=PermissionListResponseSchema,
            responses={
                500: {
                    'model': ErrorResponse
                }
            })
async def list_permissions(*,
                           _: TokenUser = Depends(AuthorizeTokenUser()),
                           session: AsyncSession = Depends(get_session)):
    """
    All List Permissions API
    """

    # Database Instance
    perm_dal = PermissionsDAL(session=session)

    try:
        # TODO: Pagination
        perm_list = await perm_dal.list()
        responses = PermissionListResponseSchema(data=[
            PermissionBaseSchema(id=perm.id,
                                 name=perm.name,
                                 slug=perm.slug,
                                 content=perm.content,
                                 created_at=perm.created_at,
                                 updated_at=perm.updated_at)
            for perm in perm_list])
    except Exception as e:
        logger.exception(e)
        return CustomJSONResponse(message='Internal Server Error',
                                  status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return responses


@router.post('',
             response_model=DefaultResponse,
             responses={
                 401: {
                     'model': ErrorResponse
                 },
                 409: {
                     'model': ErrorResponse
                 },
                 500: {
                     'model': ErrorResponse
                 }
             })
async def create_permissions(*,
                             perm_info: PermissionCreateUpdateRequestSchema,
                             _: TokenUser =Depends(AuthorizeTokenUser()),
                             session: AsyncSession = Depends(get_session)):
    """
    Create Permissions API
    """

    # Database Instance
    perm_dal = PermissionsDAL(session=session)
    # Slug 생성
    perm_info.slug = slugify(perm_info.name)

    try:
        try:
            perm_result = await perm_dal.insert(permission=perm_info)
        except IntegrityError as e:
            logger.exception(e)
            await session.rollback()
            return CustomJSONResponse(message='permission already exists',
                                      status_code=status.HTTP_409_CONFLICT)

        try:
            perm_id = perm_result.inserted_primary_key[0]
        except IndexError as e:
            logger.exception(e)
            await session.rollback()
            return CustomJSONResponse(message='permission create failed',
                                      status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

        await session.commit()

    except Exception as e:
        logger.exception(e)
        await session.rollback()
        return CustomJSONResponse(message='Internal Server Error',
                                  status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    finally:
        await session.close()

    # Add Location Header
    new_resource_uri = router.url_path_for('get_permissions', perm_id=perm_id)
    headers = {'Location': new_resource_uri}

    return CustomJSONResponse(message=None,
                              status_code=status.HTTP_201_CREATED,
                              headers=headers)


@router.get('/{perm_id}',
            response_model=PermissionBaseSchema,
            responses={
                401: {
                    'model': ErrorResponse
                },
                404: {
                    'model': ErrorResponse
                },
                500: {
                    'model': ErrorResponse
                }
            })
async def get_permissions(*,
                          perm_id: int,
                          _: TokenUser =Depends(AuthorizeTokenUser()),
                          session: AsyncSession = Depends(get_session)):
    """
    Get Permissions API
    """

    # Database Instance
    perm_dal = PermissionsDAL(session=session)

    try:
        perm = await perm_dal.get(perm_id)

    except Exception as e:
        logger.exception(e)
        await session.rollback()
        return CustomJSONResponse(message='Internal Server Error',
                                  status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    else:
        if not perm:
            logger.info(f'permission not found { {"permission_id": perm_id} }')
            return CustomJSONResponse(message='Permission not found',
                                      status_code=status.HTTP_404_NOT_FOUND)
    finally:
        await session.close()

    response = PermissionBaseSchema(id=perm.id,
                                    name=perm.name,
                                    content=perm.content,
                                    created_at=perm.created_at,
                                    updated_at=perm.updated_at)
    return response


@router.put('/{perm_id}',
            response_model=PermissionBaseSchema,
            responses={
                401: {
                    'model': ErrorResponse
                },
                404: {
                    'model': ErrorResponse
                },
                500: {
                    'model': ErrorResponse
                }
            })
async def update_permissions(*,
                             perm_id: int,
                             perm_info: PermissionCreateUpdateRequestSchema,
                             _: TokenUser =Depends(AuthorizeTokenUser()),
                             session: AsyncSession = Depends(get_session)):
    """
    Update Permissions API
    """

    # Database Instance
    perm_dal = PermissionsDAL(session=session)
    # Slug 생성
    perm_info.slug = slugify(perm_info.name)

    try:
        await perm_dal.update(perm_id=perm_id, permission=perm_info)

        await session.commit()
    except Exception as e:
        logger.exception(e)
        await session.rollback()
        return CustomJSONResponse(message='Internal Server Error',
                                  status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    else:
        perm = await perm_dal.get(perm_id=perm_id)
        if not perm:
            logger.info(f'permission not found { {"permission_id": perm_id} }')
            return CustomJSONResponse(message='Permission not found',
                                      status_code=status.HTTP_404_NOT_FOUND)
    finally:
        await session.close()

    response = PermissionBaseSchema(id=perm.id,
                                    name=perm.name,
                                    content=perm.content,
                                    created_at=perm.created_at,
                                    updated_at=perm.updated_at)
    return response


@router.delete('/{perm_id}',
               status_code=status.HTTP_204_NO_CONTENT,
               responses={
                   401: {
                       'model': ErrorResponse
                   },
                   403: {
                       'model': ErrorResponse
                   },
                   404: {
                       'model': ErrorResponse
                   },
                   500: {
                       'model': ErrorResponse
                   }
               })
async def delete_permissions(*,
                             perm_id: int,
                             _: TokenUser = Depends(AuthorizeTokenUser()),
                             session: AsyncSession = Depends(get_session)):
    """
    Delete Permissions API
    """

    # Database Instance
    role_dal = RolesDAL(session=session)
    perm_dal = PermissionsDAL(session=session)
    role_perm_dal = RolesPermissionsDAL(session=session)

    try:
        perm = await perm_dal.get(perm_id=perm_id)
        if not perm:
            logger.info(f'permission not found { {"permission_id": perm_id} }')
            return CustomJSONResponse(message='Permission not found',
                                      status_code=status.HTTP_404_NOT_FOUND)

        # Permission과 연결된 Role이 존재하는지 확인한다
        if await role_perm_dal.exists_relation_roles(perm_id=perm_id):
            # Permission 과 연결된 Role의 이름을 가져온다
            result = await role_dal.get_roles_relation_permissions(perm_id=perm_id)
            msg = ', '.join(result)
            if msg:
                logger.info(f"can't delete permission. because '{msg}' currently assigned to this permission")
                return CustomJSONResponse(message=f"can't delete permission. "
                                                  f"because '{msg}' currently assigned to this permission",
                                          status_code=status.HTTP_403_FORBIDDEN)

        await perm_dal.delete(perm_id=perm_id)

        await session.commit()
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.exception(e)
        await session.rollback()
        return CustomJSONResponse(message='Internal Server Error',
                                  status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    finally:
        await session.close()
