// This script configures Nexus to be ready for Cachito and is idempotent
import com.google.common.collect.Sets
import groovy.json.JsonSlurper
import org.sonatype.nexus.repository.config.Configuration
import org.sonatype.nexus.repository.storage.WritePolicy
import org.sonatype.nexus.security.authz.AuthorizationManager
import org.sonatype.nexus.security.role.NoSuchRoleException
import org.sonatype.nexus.security.role.Role
import static org.sonatype.nexus.security.user.UserManager.DEFAULT_SOURCE
import org.sonatype.nexus.security.user.UserNotFoundException
import org.sonatype.nexus.security.user.UserStatus


def createUser(String username, String password, List<String> roles) {
    try {
        def user = security.securitySystem.getUser(username)
        user.setFirstName(username)
        user.setLastName(username)
        user.setEmailAddress('noreply@domain.local')
        user.setStatus(UserStatus.active)
        security.securitySystem.updateUser(user)
        security.setUserRoles(username, roles)
        security.securitySystem.changePassword(username, password)
    } catch (UserNotFoundException e) {
        String firstName = username
        String lastName = username
        String email = 'noreply@domain.local'
        Boolean active = true
        security.addUser(username, firstName, lastName, email, active, password, roles)
    }
}


def createRole(String name, String description, List<String> privileges) {
    // repository is an object that is injected by Nexus when the script is executed
    AuthorizationManager authorizationManager = security.securitySystem.getAuthorizationManager(DEFAULT_SOURCE)

    String roleID = name
    try {
        Role role = authorizationManager.getRole(roleID)
        role.privileges = Sets.newHashSet(privileges)
        authorizationManager.updateRole(role)
    } catch (NoSuchRoleException e) {
        List<String> roles = []
        security.addRole(roleID, name, description, privileges, roles)
    }
}



def createBlobStore(String blobStoreName) {
    if (!blobStore.blobStoreManager.get(blobStoreName)) {
        // Name the directory the same as the blobStoreName for where the blob store will be located.
        // If an absolute path is not provided such as in this case, it will be located at $NEXUS_DATA/<blobStorePath>.
        String blobStorePath = blobStoreName
        blobStore.createFileBlobStore(blobStoreName, blobStorePath)
    }
}


def createHostedNpmRepo(String repoName, String blobStoreName) {
    WritePolicy writePolicy = WritePolicy.ALLOW_ONCE
    Boolean strictContentValidation = true
    // repository is an object that is injected by Nexus when the script is executed
    if(repository.repositoryManager.exists(repoName)) {
        Configuration hostedRepoConfig = repository.repositoryManager.get(repoName).configuration
        def storage = hostedRepoConfig.attributes('storage')
        storage.set('strictContentTypeValidation', strictContentValidation)
        storage.set('writePolicy', writePolicy)
        repository.repositoryManager.update(hostedRepoConfig)
    }
    else {
        repository.createNpmHosted(repoName, blobStoreName, strictContentValidation, writePolicy)
    }
}


def createProxyNpmRepo(String repoName, String registry, String blobStoreName) {
    /*
        Here are some of the properties that can be set:
          repositoryName: name,
          recipeName: recipeName,
          online: true,
          attributes: [
            httpclient: [
              authentication: <See Below>,
              autoBlock: <Boolean>,
              blocked: <Boolean>,
              connection: https://bit.ly/39hLQBZ
            ],
            proxy: [
              remoteUrl: <String>,
              contentMaxAge: <Integer>,
              metadataMaxAge: <Integer>
            ],
            negativeCache: [
              enabled: <Boolean>,
              timeToLive: <Integer>
            ],
            storage: [
              blobStoreName: <String>,
              strictContentTypeValidation: <Boolean>
            ]
          ]

        httpclient.authentication (select from one of the following types):
        - username: https://bit.ly/2WMAxPB
        - ntlm: https://bit.ly/3bC87w7
        - bearerToken: https://bit.ly/2QL4egm

        For example:
          def authentication = repo_config.attributes('httpclient').child('authentication')
          authentication.set('type', 'username')
          authentication.set('username', 'cachito')
          authentication.set('password', 'secret_password')
     */
    Configuration proxyRepoConfig
    // repository is an object that is injected by Nexus when the script is executed
    Boolean repoExists = repository.repositoryManager.exists(repoName)
    if(repoExists) {
        proxyRepoConfig = repository.repositoryManager.get(repoName).configuration
    }
    else {
        proxyRepoConfig = repository.repositoryManager.newConfiguration()
        proxyRepoConfig.repositoryName = repoName
        proxyRepoConfig.recipeName = 'npm-proxy'
        proxyRepoConfig.attributes('storage').set('blobStoreName', blobStoreName)
    }

    proxyRepoConfig.online = true
    proxyRepoConfig.attributes('storage').set('strictContentTypeValidation', true)

    def proxy = proxyRepoConfig.attributes('proxy')
    proxy.set('remoteUrl', registry)
    // Don't expire the cache
    proxy.set('contentMaxAge', -1)
    proxy.set('metadataMaxAge', -1)

    def httpclient = proxyRepoConfig.attributes('httpclient')
    httpclient.set('autoBlock', true)
    httpclient.set('blocked', false)

    proxyRepoConfig.attributes('negativeCache').set('enabled', false)

    if(repoExists) {
        repository.repositoryManager.update(proxyRepoConfig)
    }
    else {
        repository.repositoryManager.create(proxyRepoConfig)
    }
}


def createGroupNpmRepo(String repoName, List<String> groupMembers, String blobStoreName) {
    // repository is an object that is injected by Nexus when the script is executed
    if(repository.repositoryManager.exists(repoName)) {
        Configuration groupRepoConfig = repository.repositoryManager.get(repoName).configuration
        groupRepoConfig.attributes('group').set('memberNames', groupMembers)
        repository.repositoryManager.update(groupRepoConfig)
    } else {
        repository.createNpmGroup(repoName, groupMembers, blobStoreName)
    }
}


def createCachitoUser(String password) {
    String description = 'Admin access on all the repositories'
    List<String> privileges = [
            // This is required so that Cachito can reset a created user's password if a Groovy script is executed again
            // due to a worker crashing and rerunning the task. This is necessary because Cachito will generate the
            // password as part of the task and not store it in the database.
            'nx-userschangepw',
            // This is so that Cachito can create, read, update, and delete Nexus repositories
            'nx-repository-admin-*-*-*',
            // This is so that Cachito can create, read, update, and delete Nexus roles
            'nx-roles-all',
            // This is so that Cachito can create, read, update, and delete Nexus Groovy scripts
            'nx-script-*-*',
            // This is so that Cachito can create, read, update, and delete Nexus users
            'nx-users-all',
    ]
    createRole('cachito', description, privileges)

    createUser('cachito', password, ['cachito'])
}


def createCachitoJsUser(String password) {
    // This creates an unprivileged user that just has the ability to use the cachito-js Nexus repository. This is
    // the account used for authentication against the cachito-js Nexus repository for the NPM proxy repositories
    // created per Cachito request.
    List<String> privileges = [
        // This allows the unprivileged user to use the cachito-js Nexus repository
        'nx-repository-view-npm-cachito-js-*'
    ]
    String description = 'The user that can use the the cachito-js repository'
    createRole('cachito_js', description, privileges)

    createUser('cachito_js', password, ['cachito_js'])
}


// Main execution starts here
def request = new JsonSlurper().parseText(args)
['base_url', 'cachito_js_password', 'cachito_password'].each { param ->
    assert request.get(param): "The ${param} parameter is required"
    assert request.get(param) instanceof String: "The ${param} parameter must be a string"
}
if (request.new_admin_password) {
    assert request.new_admin_password instanceof String: 'The new_admin_password parameter must be a string'
}

// Set the baseURL to prevent exceptions in the remaining code
// core is an object that is injected by Nexus when the script is executed
core.baseUrl(request.base_url)

// Set the admin user's password if requested
if (request.new_admin_password) {
    // security is an object that is injected by Nexus when the script is executed
    security.securitySystem.changePassword('admin', request.new_admin_password)
}

// security is an object that is injected by Nexus when the script is executed
security.setAnonymousAccess(false)

String blobStoreName = 'cachito-js'
createBlobStore(blobStoreName)

String hostedRepoName = 'cachito-js-hosted'
createHostedNpmRepo(hostedRepoName, blobStoreName)

String proxyRepoName = 'cachito-js-proxy'
String registry = 'https://registry.npmjs.org'
createProxyNpmRepo(proxyRepoName, registry, blobStoreName)

String groupRepoName = 'cachito-js'
List<String> groupMembers = [hostedRepoName, proxyRepoName]
createGroupNpmRepo(groupRepoName, groupMembers, blobStoreName)

createCachitoUser(request.cachito_password)
createCachitoJsUser(request.cachito_js_password)

return "Nexus was configured successfully"
